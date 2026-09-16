"""Running one turn: notifications out, approvals in.

The session never schedules anything. Approval arrives through
:class:`~super_agent.runtime.execution.ApprovalWaiter`, which the engine calls from
inside a scheduled action, which is why there is no second loop here.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from super_agent.errors import Cancelled
from super_agent.runtime.engine import Engine, EngineView
from super_agent.runtime.execution import (
    ApprovalWaiter,
    ErrApprovalDismissed,
    PermissionRequest,
)
from super_agent.runtime.machine import ApprovalDecision, StreamChunk, ToolCall, UserMessageSubmitted
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Attachment
from super_agent.runtime.session.notifications import (
    NOTIFICATIONS_CLOSED,
    SessionError,
    SessionNotification,
    StreamChunkReceived,
)

if TYPE_CHECKING:
    from super_agent.runtime.session.repository import Repository, SessionID
    from super_agent.runtime.session.snapshot_emitter import SnapshotEmitter


class ApprovalsClosed:
    """Pushed onto the approvals queue when the interface stops offering decisions.

    An :class:`asyncio.Queue` cannot be closed, so the close is delivered as a
    value; :func:`waitApproval` turns it into
    :data:`~super_agent.runtime.execution.ErrApprovalDismissed`.
    """


#: The close marker for the approvals queue.
APPROVALS_CLOSED = ApprovalsClosed()


async def waitApproval(
    ctx: RunContext, approvals: asyncio.Queue[ApprovalDecision | ApprovalsClosed]
) -> ApprovalDecision:
    """Wait for a decision or for the run to be cancelled, whichever is first.

    A closed queue means the interface went away, which the engine treats as a
    cancellation rather than a fault — the difference matters because a fault is
    reported to the model as a tool error and a cancellation is not.
    """
    getter = asyncio.ensure_future(approvals.get())
    cancelled = asyncio.ensure_future(ctx.Done().wait())
    try:
        done, _pending = await asyncio.wait({getter, cancelled}, return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            decision = getter.result()
            if isinstance(decision, ApprovalsClosed):
                error = ctx.Err()
                if error is not None:
                    raise error
                raise ErrApprovalDismissed
            return decision
        raise Cancelled("run cancelled")
    finally:
        for waiter in (getter, cancelled):
            if not waiter.done():
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter


class TurnMixin:
    """``RunTurn`` and its failure path."""

    if TYPE_CHECKING:
        engine: Engine
        emitter: SnapshotEmitter
        repository: Repository | None
        _attachments: list[Attachment]

        def _tryLock(self) -> bool: ...
        def _unlock(self) -> None: ...
        def metaID(self) -> SessionID: ...
        def Snapshot(self) -> EngineView: ...
        async def emitSnapshot(self, notifications: asyncio.Queue[SessionNotification]) -> None: ...
        def persistTurnBoundary(self) -> None: ...
        def persistMessage(self, message: object) -> None: ...
        def persistApproval(self, decision: ApprovalDecision, call: ToolCall) -> None: ...
        def persistError(self, error: BaseException | None) -> None: ...

    async def RunTurn(
        self,
        ctx: RunContext,
        query: str,
        notifications: asyncio.Queue[SessionNotification],
        approvals: asyncio.Queue[ApprovalDecision | ApprovalsClosed],
    ) -> None:
        """Run one turn, publishing notifications and consuming approval decisions."""
        try:
            if not self._tryLock():
                raise RuntimeError("session is already running a turn")
            try:
                self.persistTurnBoundary()

                async def observer() -> None:
                    await self.emitSnapshot(notifications)  # type: ignore[attr-defined]

                # Track live state transitions while actions drain: states such as
                # RunningTool and AdvancingQueue pass between snapshot points, and
                # the header should follow them as they happen.
                self.engine.SetStateObserver(observer)
                try:
                    error = await self._run(ctx, query, notifications, approvals)
                finally:
                    self.engine.SetStateObserver(None)
            finally:
                self._unlock()
        finally:
            # The end-of-turn close, registered first and therefore run last: the
            # observer is already cleared and the turn lock released by the time
            # the consumer sees this.
            #
            # A bounded queue can be full here, and raising `QueueFull` from a
            # `finally` would replace whatever the turn actually produced — the
            # error the caller must see would be masked by a delivery detail. The
            # delivery is therefore best effort; the authoritative end-of-turn
            # signal is this call returning, which callers and the notification
            # bridge both observe.
            with contextlib.suppress(asyncio.QueueFull):
                notifications.put_nowait(NOTIFICATIONS_CLOSED)

        if error is not None:
            raise error

    async def _run(
        self,
        ctx: RunContext,
        query: str,
        notifications: asyncio.Queue[SessionNotification],
        approvals: asyncio.Queue[ApprovalDecision | ApprovalsClosed],
    ) -> BaseException | None:
        def onStreamChunk(chunk: StreamChunk) -> None:
            # A full queue drops this chunk rather than blocking the model. The
            # notification carries the whole accumulated message, so the consumer
            # converges on the same text from the next one.
            notification = StreamChunkReceived(Chunk=chunk, Message=self.Snapshot().StreamingMessage)
            with contextlib.suppress(asyncio.QueueFull):
                notifications.put_nowait(notification)

        waiter = self._approvalWaiter(approvals)

        attachments = list(self._attachments)
        self._attachments = []

        try:
            await self.engine.RunTurn(
                ctx,
                UserMessageSubmitted(Content=query, Attachments=tuple(attachments)),
                onStreamChunk,
                waiter,
            )
        except Exception as failure:
            error = self.failTurn(notifications, failure)
        else:
            error = None
        await self.emitter.emit(notifications, self.engine.Snapshot(), self.persistMessage)
        return error

    def _approvalWaiter(self, approvals: asyncio.Queue[ApprovalDecision | ApprovalsClosed]) -> ApprovalWaiter:
        """Bridge the queue to the engine's approval port.

        The decision is persisted and the emitter re-armed *before* the engine
        applies it, so an audit record exists even if the run is cancelled in the
        next instant, and the next identical call prompts again.
        """

        async def wait(ctx: RunContext, call: ToolCall, _request: PermissionRequest) -> ApprovalDecision:
            decision = await waitApproval(ctx, approvals)
            self.persistApproval(decision, call)
            self.emitter.markApprovalConsumed()
            return decision

        return _WaitApprovalFunc(wait)

    def failTurn(self, notifications: asyncio.Queue[SessionNotification], failure: BaseException) -> BaseException:
        notifications.put_nowait(SessionError(Err=failure))
        self.persistError(failure)
        return failure


class _WaitApprovalFunc:
    """Adapts a plain async function to the ``ApprovalWaiter`` protocol."""

    __slots__ = ("_wait",)

    def __init__(self, wait: Callable[[RunContext, ToolCall, PermissionRequest], Awaitable[ApprovalDecision]]) -> None:
        self._wait = wait

    async def WaitApproval(self, ctx: RunContext, call: ToolCall, request: PermissionRequest) -> ApprovalDecision:
        return await self._wait(ctx, call, request)
