"""The single agent loop.

``run_turn`` starts it, and ``run_scheduled_actions`` drains the queue until the
queue is empty *and* the state is ``Idle``. An empty queue in any other state is
not a quiet finish, it is an invariant violation: something scheduled work that
never ran, or ran without scheduling its successor.

Every completed action is filtered by run id before it is allowed to change
anything. That filter is what stops a cancelled turn from writing into the turn
that replaced it.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from super_agent.errors import Cancelled, errors_is
from super_agent.runtime.execution import (
    ERR_APPROVAL_DISMISSED,
    ActionResultInput,
    ActionResultResolver,
    ApprovalStore,
    ApprovalWaiter,
    ModelReplied,
    QueuedAction,
    RunController,
    RunID,
    ScheduledActionInput,
    ScheduledActionRunner,
    new_approval_key,
)
from super_agent.runtime.machine import (
    STATE_IDLE,
    AppendStreamingAssistant,
    ApprovalAlwaysGranted,
    AwaitApproval,
    CallModel,
    CancelRequested,
    ErrorOccurred,
    Event,
    InvariantViolationError,
    MachineSnapshot,
    Message,
    RuntimeData,
    RunTool,
    State,
    ToolCallBatch,
    TransitionResult,
    UserMessageSubmitted,
    snapshot_from,
    transition,
    validate_runtime_data,
)
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import ToolSpec, Usage
from super_agent.runtime.telemetry import telemetry

if TYPE_CHECKING:
    from super_agent.runtime.machine import RuntimeDataChangeApplier


def typeName(value: Any) -> str:
    """The value's type name, or ``"nil"`` when it is ``None``."""
    return "nil" if value is None else type(value).__name__


def errorString(error: BaseException | None) -> str:
    return "" if error is None else str(error)


def estimateTokens(value: str) -> int:
    """Four characters per token, a deliberately rough divisor."""
    count = len(value)
    if count == 0:
        return 0
    return (count + 3) // 4


def estimateMessageTokens(messages: tuple[Message, ...] | list[Message]) -> int:
    return sum((len(message.content) + len(message.reasoning_content) + 3) // 4 for message in messages)


def cloneToolBatch(batch: ToolCallBatch | None) -> ToolCallBatch | None:
    """A copy the resolver may read while the queue keeps advancing."""
    if batch is None:
        return None
    return ToolCallBatch(id=batch.id, calls=list(batch.calls), index=batch.index)


def millis_since(started: float) -> int:
    """Elapsed milliseconds, truncated toward zero."""
    return int((time.monotonic() - started) * 1000)


def _is_cancellation(error: BaseException) -> bool:
    """Cancellation is decided by error identity, not by action type.

    A dropped approval and a cancelled context both cancel the run. A
    misconfigured waiter, or anything else, takes the error path — which is what
    answers the outstanding tool calls.
    """
    return (
        errors_is(error, Cancelled)
        or errors_is(error, ERR_APPROVAL_DISMISSED)
        or isinstance(error, asyncio.CancelledError)
    )


class ActionLoopMixin:
    """The single agent loop and the event dispatch it serves."""

    if TYPE_CHECKING:
        # Provided by Engine.__init__.
        _action_queue: Any
        _applier: RuntimeDataChangeApplier
        _approvals: ApprovalStore
        _resolver: ActionResultResolver
        _runner: ScheduledActionRunner
        _runtime_data: RuntimeData
        _runs: RunController
        lock: Any

        def messages(self) -> list[Message]: ...
        async def _notify_state_observer(self) -> None: ...
        def _notify_model_usage_observer(self, usage: Usage | None) -> None: ...

    async def dispatch_event(
        self,
        ctx: RunContext,
        event: Event,
        on_stream_chunk: Callable[[Any], None] | None,
    ) -> None:
        """Route an external event into the machine.

        ``UserMessageSubmitted`` is refused here: starting a turn and starting a
        run are the same act, and only :meth:`run_turn` performs both.
        """
        if isinstance(event, UserMessageSubmitted):
            raise ValueError("user messages must be submitted through Engine.RunTurn")
        _run_id, error = await self._dispatch_event(ctx, event, on_stream_chunk, None)
        if error is not None:
            raise error

    async def run_turn(
        self,
        ctx: RunContext,
        event: UserMessageSubmitted,
        on_stream_chunk: Callable[[Any], None] | None,
        approval_waiter: ApprovalWaiter | None,
    ) -> None:
        """Run one user turn to completion.

        The run id is captured as soon as the run starts, so the telemetry record
        names this turn even when a cancel path bumps the id later.
        """
        started = time.monotonic()
        run_id, error = await self._dispatch_event(ctx, event, on_stream_chunk, approval_waiter)
        telemetry.record(
            "run",
            {
                "run_id": str(run_id),
                "duration_ms": millis_since(started),
                "error": errorString(error),
            },
        )
        if error is not None:
            raise error

    async def _dispatch_event(
        self,
        ctx: RunContext,
        event: Event,
        on_stream_chunk: Callable[[Any], None] | None,
        approval_waiter: ApprovalWaiter | None,
    ) -> tuple[RunID, Exception | None]:
        """Apply one transition, then drain the queue it scheduled.

        Returns the error rather than raising it so the caller can record
        telemetry with the run id that was live when the failure happened. Only
        ``Exception`` is returned; cancellation of the surrounding task
        propagates.
        """
        async with self.lock:
            decision = self._calculate_transition_locked(event)

            run_ctx = ctx
            started_run = False
            if isinstance(event, UserMessageSubmitted):
                _, run_ctx = self._runs.start_run(ctx)
                started_run = True
            elif decision.action_plan.schedule:
                current_ctx, active = self._runs.current_context()
                if not active or current_ctx is None:
                    raise RuntimeError("event scheduled actions without an active run")
                run_ctx = current_ctx

            try:
                self._commit_transition_locked(decision)
            except BaseException:
                # A run that could not commit its first transition must not be
                # left live: nothing is going to finish it.
                if started_run:
                    self._runs.cancel_run()
                raise

            state = self._runtime_data.state
            run_id = self._runs.current_run_id()

        telemetry.record(
            "transition",
            {
                "run_id": str(run_id),
                "event": typeName(event),
                "state": str(state),
                "scheduled_actions": len(decision.action_plan.schedule),
            },
        )
        await self._notify_state_observer()
        try:
            await self._run_scheduled_actions(run_ctx, on_stream_chunk, approval_waiter)
        except Exception as error:
            return run_id, error
        return run_id, None

    def _calculate_transition_locked(self, event: Event) -> TransitionResult:
        snapshot: MachineSnapshot = snapshot_from(self._runtime_data)
        return transition(snapshot, event)

    def _commit_transition_locked(self, decision: TransitionResult) -> None:
        """Commit runtime data and the action plan as one decision.

        The data changes, the queue clear, and the new queue entries all happen
        inside one lock hold, so the queue can never belong to a state that no
        longer exists.
        """
        change_result = self._applier.apply_runtime_data_changes(self._runtime_data, decision)
        validate_runtime_data(change_result.runtime_data)
        self._runtime_data = change_result.runtime_data
        if decision.action_plan.clear_existing:
            self._action_queue.clear()
        for action in decision.action_plan.schedule:
            self._action_queue.queue(self._runs.current_run_id(), action)

    async def _run_scheduled_actions(
        self,
        ctx: RunContext,
        on_stream_chunk: Callable[[Any], None] | None,
        approval_waiter: ApprovalWaiter | None,
    ) -> None:
        run_id = self._runs.current_run_id()
        while True:
            async with self.lock:
                action = self._action_queue.pop()
                if action is None:
                    if self._runtime_data.state == STATE_IDLE:
                        self._runs.finish_run(run_id)
                        return
                    state: State = self._runtime_data.state
                    raise InvariantViolationError(f"action queue is empty in state {state}")

            try:
                await self._execute_scheduled_action(ctx, action, on_stream_chunk, approval_waiter)
            except BaseException as failure:
                if _is_cancellation(failure):
                    self._runs.cancel_run()
                    await self._dispatch_ignoring_failure(ctx, CancelRequested())
                else:
                    await self._dispatch_ignoring_failure(ctx, ErrorOccurred(err=failure))
                raise
            # The action may have committed a transition; notify so observers see
            # the states that pass between snapshot points, such as RunningTool
            # while a tool executes.
            await self._notify_state_observer()

    async def _dispatch_ignoring_failure(self, ctx: RunContext, event: Event) -> None:
        """Report a terminal event, discarding whatever it reports back.

        Every exception is suppressed, cancellation included, because the failure
        the caller must see is the original one — a second error raised from the
        cleanup path would replace it.
        """
        with contextlib.suppress(BaseException):
            await self.dispatch_event(ctx, event, None)

    async def _execute_scheduled_action(
        self,
        ctx: RunContext,
        action: QueuedAction,
        on_stream_chunk: Callable[[Any], None] | None,
        approval_waiter: ApprovalWaiter | None,
    ) -> None:
        started = time.monotonic()
        stream: Callable[[Any], None] | None = on_stream_chunk
        if on_stream_chunk is not None:
            callback = on_stream_chunk

            def teed(chunk: Any, _run_id: RunID = action.run_id, _cb: Callable[[Any], None] = callback) -> None:
                self._record_stream_chunk(_run_id, chunk)
                _cb(chunk)

            stream = teed

        env = ScheduledActionInput(
            messages=tuple(self.messages()),
            tool_specs=tuple(self._tool_specs()),
            approval_waiter=approval_waiter,
        )
        action_ctx = telemetry.with_i_ds(ctx, str(action.run_id), str(action.action_id))
        try:
            completion = await self._runner.run(action_ctx, action, env, stream)  # type: ignore[arg-type]
        except BaseException as error:
            telemetry.record(
                "action",
                {
                    "run_id": str(action.run_id),
                    "action_id": str(action.action_id),
                    "action": typeName(action.action),
                    "duration_ms": millis_since(started),
                    "error": str(error),
                },
            )
            raise

        fields: dict[str, Any] = {
            "run_id": str(action.run_id),
            "action_id": str(action.action_id),
            "action": typeName(action.action),
            "duration_ms": millis_since(started),
        }
        if isinstance(action.action, CallModel):
            fields["component"] = "model"
        elif isinstance(action.action, RunTool):
            fields["component"] = "tool"
            fields["tool"] = action.action.call.name
        elif isinstance(action.action, AwaitApproval):
            fields["component"] = "approval"

        if isinstance(completion.result, ModelReplied):
            fields["input_tokens_estimate"] = estimateMessageTokens(env.messages)
            fields["output_tokens_estimate"] = estimateTokens(
                completion.result.response.content + completion.result.response.reasoning_content
            )
            # Exact provider counts win over the rune-count estimates when the
            # adapter could obtain them.
            fields.update(_usage_fields(completion.result.response.usage))
        telemetry.record("action", fields)

        if not self._runs.is_current(completion.run_id):
            # A stale completion is discarded, not applied. This is the whole
            # mechanism that keeps a cancelled turn out of the next one.
            return

        if isinstance(completion.result, ModelReplied):
            # The interface hears what a model call cost only while that call
            # still belongs to the conversation.
            self._notify_model_usage_observer(completion.result.response.usage)

        # Specs are fetched outside the engine lock on purpose: a registry change
        # (an MCP reconnect, say) can block Specs for seconds, and the lock must
        # stay free for queries and dispatch. The cost is that this one resolution
        # may classify against a spec set that just changed — benign, since the
        # next action re-fetches.
        tool_specs = self._tool_specs()
        async with self.lock:
            batch = cloneToolBatch(self._runtime_data.tool_batch)
            event = self._resolver.resolve(
                completion.result,  # type: ignore[arg-type]
                ActionResultInput(tool_batch=batch, tool_specs=tuple(tool_specs)),
            )
            decision = self._calculate_transition_locked(event)
            self._commit_transition_locked(decision)

        if isinstance(event, ApprovalAlwaysGranted):
            self._approvals.allow_always(new_approval_key(event.call))

    def _tool_specs(self) -> list[ToolSpec]:
        return self._runner.tool_specs()

    def _record_stream_chunk(self, run_id: RunID, chunk: Any) -> None:
        """Append streaming content, re-checking staleness under the lock.

        A reset that lands between the stream callback and this commit must not
        append into a conversation that has already been cleared.

        No lock is taken: this method never awaits, so the event loop cannot
        switch tasks inside it.
        """
        if not self._runs.is_current(run_id):
            return
        try:
            self._commit_transition_locked(
                TransitionResult(
                    next_state=self._runtime_data.state,
                    runtime_data_changes=(AppendStreamingAssistant(chunk=chunk),),
                )
            )
        except Exception as error:
            telemetry.record("stream_chunk_rejected", {"run_id": str(run_id), "error": str(error)})


def _usage_fields(usage: Usage | None) -> dict[str, Any]:
    if usage is None:
        return {}
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }


# Re-exported so callers of the engine do not import the context module twice.
__all__ = [
    "ActionLoopMixin",
    "cloneToolBatch",
    "errorString",
    "estimateMessageTokens",
    "estimateTokens",
    "millis_since",
    "typeName",
]
