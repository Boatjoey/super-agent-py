"""Engine commands: everything the outside world asks the engine to do.

``Cancel`` and ``Reset`` retire the current run *before* dispatching, so an action
still in flight can never write into the state the command just established.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

from super_agent.runtime.engine.policy_ports import PolicySetter, PolicyStore
from super_agent.runtime.execution import (
    ActionCompletion,
    ActionQueue,
    ActionResultResolver,
    ApprovalStore,
    ModelReplied,
    NewPolicy,
    PermissionMode,
    PermissionRules,
    QueuedAction,
    RunController,
    ScheduledActionInput,
    ScheduledActionRunner,
    ValidPermissionMode,
)
from super_agent.runtime.machine import (
    CallModel,
    CancelRequested,
    EngineReady,
    Event,
    Message,
    ResetRequested,
    RoleUser,
    StateIdle,
)
from super_agent.runtime.protocol.run_context import LiveContext, RunContext
from super_agent.runtime.protocol.types import StreamChunk

if TYPE_CHECKING:
    from super_agent.runtime.machine import RuntimeData

#: The prompt ``/compact`` uses when the user gave no summary of their own.
COMPACT_SUMMARY_PROMPT = (
    "Summarize this conversation for context compaction. Preserve goals, decisions, "
    "files changed, tool results, and unresolved next steps."
)


def discard_chunk(_chunk: StreamChunk) -> None:
    """A no-op stream callback; the model must not call it."""


class CommandsMixin:
    """Engine commands."""

    if TYPE_CHECKING:
        # Provided by Engine.__init__.
        _action_queue: ActionQueue
        _approvals: ApprovalStore
        _resolver: ActionResultResolver
        _runner: ScheduledActionRunner
        _runtime_data: RuntimeData
        _runs: RunController
        lock: asyncio.Lock

        async def DispatchEvent(
            self, ctx: RunContext, event: Event, on_stream_chunk: Callable[[StreamChunk], None] | None
        ) -> None: ...
        def Messages(self) -> list[Message]: ...

    async def Ready(self) -> None:
        """Move the machine out of ``Initializing``."""
        await self.DispatchEvent(LiveContext(), EngineReady(), None)

    async def Cancel(self) -> None:
        """Retire the current run, then tell the machine the run was cancelled.

        The order matters: the run id is bumped first, so any completion already in
        flight is stale by the time the cancel transition lands, and the cancel
        transition's own answer to the outstanding tool calls is the only one
        applied.
        """
        self._runs.CancelRun()
        await self.DispatchEvent(LiveContext(), CancelRequested(), None)

    async def Reset(self) -> None:
        """Cancel the run and clear the conversation, keeping system messages."""
        self._runs.CancelRun()
        self._runs.InvalidateCurrentRun()
        await self.DispatchEvent(LiveContext(), ResetRequested(), None)

    async def ReplaceMessages(self, messages: list[Message]) -> None:
        """Replace the conversation wholesale, without a transition.

        Used by resume and compaction, which already have the messages they want
        and no event that would produce them. Clearing the tool slots and the
        action queue is what keeps a late action from a conversation that no
        longer exists out of the replacement.
        """
        self._runs.CancelRun()
        self._runs.InvalidateCurrentRun()
        async with self.lock:
            data = self._runtime_data
            data.Messages = list(messages)
            data.PendingTool = None
            data.PendingPermission = None
            data.CurrentTool = None
            data.ToolBatch = None
            data.StreamingContent = ""
            data.StreamingReasoning = ""
            data.State = StateIdle
            self._action_queue.Clear()

    async def SetPermissionPolicy(self, mode: PermissionMode, rules: PermissionRules) -> None:
        """Swap the policy, refusing a mode the settings loader would reject too."""
        async with self.lock:
            if not ValidPermissionMode(mode):
                raise ValueError("invalid permission mode: " + str(mode))
            if not isinstance(self._resolver, PolicySetter):
                raise ValueError("action result resolver does not support policy updates")
            self._resolver.SetPolicy(NewPolicy(mode, rules))
            if isinstance(self._approvals, PolicyStore):
                self._approvals.SetPermissionPolicy(mode, rules)

    async def CompactSummary(self, ctx: RunContext) -> str:
        """Ask the model for a summary of the conversation so far."""
        messages = self.Messages()
        if not messages:
            return ""
        completion: ActionCompletion = await self._runner.Run(
            ctx,
            QueuedAction(Action=CallModel()),
            ScheduledActionInput(Messages=(*messages, Message(Role=RoleUser, Content=COMPACT_SUMMARY_PROMPT))),
            discard_chunk,
        )
        if not isinstance(completion.Result, ModelReplied):
            raise ValueError("compact summary did not return a model response")
        summary = completion.Result.Response.Content.strip()
        if summary == "":
            summary = completion.Result.Response.ReasoningContent.strip()
        if summary == "":
            raise ValueError("compact summary is empty")
        return summary
