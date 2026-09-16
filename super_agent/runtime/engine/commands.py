"""Engine commands: everything the outside world asks the engine to do.

``cancel`` and ``reset`` retire the current run *before* dispatching, so an action
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
    PermissionMode,
    PermissionRules,
    QueuedAction,
    RunController,
    ScheduledActionInput,
    ScheduledActionRunner,
    new_policy,
    valid_permission_mode,
)
from super_agent.runtime.machine import (
    ROLE_USER,
    STATE_IDLE,
    CallModel,
    CancelRequested,
    EngineReady,
    Event,
    Message,
    ResetRequested,
)
from super_agent.runtime.protocol.run_context import RunContext, live_context
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

        async def dispatch_event(
            self, ctx: RunContext, event: Event, on_stream_chunk: Callable[[StreamChunk], None] | None
        ) -> None: ...
        def messages(self) -> list[Message]: ...

    async def ready(self) -> None:
        """Move the machine out of ``Initializing``."""
        await self.dispatch_event(live_context(), EngineReady(), None)

    async def cancel(self) -> None:
        """Retire the current run, then tell the machine the run was cancelled.

        The order matters: the run id is bumped first, so any completion already in
        flight is stale by the time the cancel transition lands, and the cancel
        transition's own answer to the outstanding tool calls is the only one
        applied.
        """
        self._runs.cancel_run()
        await self.dispatch_event(live_context(), CancelRequested(), None)

    async def reset(self) -> None:
        """Cancel the run and clear the conversation, keeping system messages."""
        self._runs.cancel_run()
        self._runs.invalidate_current_run()
        await self.dispatch_event(live_context(), ResetRequested(), None)

    async def replace_messages(self, messages: list[Message]) -> None:
        """Replace the conversation wholesale, without a transition.

        Used by resume and compaction, which already have the messages they want
        and no event that would produce them. Clearing the tool slots and the
        action queue is what keeps a late action from a conversation that no
        longer exists out of the replacement.
        """
        self._runs.cancel_run()
        self._runs.invalidate_current_run()
        async with self.lock:
            data = self._runtime_data
            data.messages = list(messages)
            data.pending_tool = None
            data.pending_permission = None
            data.current_tool = None
            data.tool_batch = None
            data.streaming_content = ""
            data.streaming_reasoning = ""
            data.state = STATE_IDLE
            self._action_queue.clear()

    async def set_permission_policy(self, mode: PermissionMode, rules: PermissionRules) -> None:
        """Swap the policy, refusing a mode the settings loader would reject too."""
        async with self.lock:
            if not valid_permission_mode(mode):
                raise ValueError("invalid permission mode: " + str(mode))
            if not isinstance(self._resolver, PolicySetter):
                raise ValueError("action result resolver does not support policy updates")
            self._resolver.set_policy(new_policy(mode, rules))
            if isinstance(self._approvals, PolicyStore):
                self._approvals.set_permission_policy(mode, rules)

    async def compact_summary(self, ctx: RunContext) -> str:
        """Ask the model for a summary of the conversation so far."""
        messages = self.messages()
        if not messages:
            return ""
        completion: ActionCompletion = await self._runner.run(
            ctx,
            QueuedAction(action=CallModel()),
            ScheduledActionInput(messages=(*messages, Message(role=ROLE_USER, content=COMPACT_SUMMARY_PROMPT))),
            discard_chunk,
        )
        if not isinstance(completion.result, ModelReplied):
            raise ValueError("compact summary did not return a model response")
        summary = completion.result.response.content.strip()
        if summary == "":
            summary = completion.result.response.reasoning_content.strip()
        if summary == "":
            raise ValueError("compact summary is empty")
        return summary
