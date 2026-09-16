"""Tool, executor, policy, and approval fakes for the engine tests.

The fakes that need to prove the engine never holds its lock while it calls out
cannot block the event loop the way a channel would, so they record what they
observe instead: :class:`LockProbeApprovalStore` and :class:`SpecProbeRunner` ask
a probe (the test's ``engine.lock``) whether the lock was held when the port ran.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence

from super_agent.errors import Cancelled
from super_agent.runtime.execution import (
    ERR_APPROVAL_DISMISSED,
    ActionCompletion,
    ApprovalKey,
    ModelReplied,
    PermissionRequest,
    QueuedAction,
    ScheduledActionInput,
    ScheduledActionResult,
    ToolDecision,
    ToolFinished,
    ToolPolicyInput,
    ToolQueueChecked,
)
from super_agent.runtime.machine import (
    ApprovalDecision,
    CallModel,
    CheckToolQueue,
    RunTool,
    ScheduledAction,
)
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Message, ModelResponse, StreamChunk, ToolCall, ToolSpec

__all__ = [
    "DenyNthPolicy",
    "FailingOnceExecutor",
    "FakeToolRunner",
    "HangingApprovalWaiter",
    "LockProbeApprovalStore",
    "RecordingExecutor",
    "RecordingPolicy",
    "ScriptedApprovalWaiter",
    "SpecProbeRunner",
    "StaticReplyExecutor",
]


class FakeToolRunner:
    """Fixed specs and results, and it records every call."""

    def __init__(self, results: Mapping[str, str] | None = None, specs: Sequence[ToolSpec] = ()) -> None:
        self.results: dict[str, str] = dict(results or {})
        self._specs: list[ToolSpec] = list(specs)
        self.calls: list[ToolCall] = []

    def specs(self) -> list[ToolSpec]:
        return self._specs

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        self.calls.append(call)
        return self.results.get(call.name, "")


class RecordingExecutor:
    """One model reply, and it records the actions it was asked to run."""

    def __init__(self) -> None:
        self.actions: list[ScheduledAction] = []

    async def execute(
        self,
        ctx: RunContext,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ScheduledActionResult:
        self.actions.append(action)
        return ModelReplied(response=ModelResponse(content="from executor"))


class FailingOnceExecutor:
    """The first call faults, later ones recover."""

    def __init__(self) -> None:
        self.calls: int = 0
        self.seen: list[Message] = []

    async def execute(
        self,
        ctx: RunContext,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ScheduledActionResult:
        self.calls += 1
        self.seen = list(env.messages)
        if self.calls == 1:
            raise RuntimeError("provider timeout")
        return ModelReplied(response=ModelResponse(content="recovered"))


class StaticReplyExecutor:
    """Always the same model reply."""

    def __init__(self, content: str = "model summary") -> None:
        self.content: str = content
        self.calls: int = 0

    def tool_specs(self) -> list[ToolSpec]:
        return []

    async def execute(
        self,
        ctx: RunContext,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ScheduledActionResult:
        self.calls += 1
        return ModelReplied(response=ModelResponse(content=self.content))


class RecordingPolicy:
    """One fixed decision, and it records its inputs."""

    def __init__(self, decision: ToolDecision) -> None:
        self.decision: ToolDecision = decision
        self.calls: list[ToolCall] = []
        self.specs: list[tuple[ToolSpec, ...]] = []

    def classify_tool_call(self, call: ToolCall, input: ToolPolicyInput) -> ToolDecision:
        self.calls.append(call)
        self.specs.append(tuple(input.tool_specs))
        return self.decision

    def permission_request(self, call: ToolCall, input: ToolPolicyInput) -> PermissionRequest:
        return PermissionRequest(tool_name=call.name, reason="test policy")


class DenyNthPolicy:
    """Run everything directly except the nth call."""

    def __init__(self, deny_at: int) -> None:
        self.deny_at: int = deny_at
        self.seen: int = 0

    def classify_tool_call(self, call: ToolCall, input: ToolPolicyInput) -> ToolDecision:
        self.seen += 1
        if self.seen == self.deny_at:
            return ToolDecision.DECISION_DENIED
        return ToolDecision.DECISION_RUN_DIRECTLY

    def permission_request(self, call: ToolCall, input: ToolPolicyInput) -> PermissionRequest:
        return PermissionRequest(tool_name=call.name, reason="denied by test policy")


class ScriptedApprovalWaiter:
    """Answers approvals from a script and records every request.

    ``on_wait`` runs while the engine is parked on the approval action, which is
    where a test can inspect engine state before sending its decision.
    """

    def __init__(
        self,
        decisions: Sequence[ApprovalDecision],
        on_wait: Callable[[], None] | None = None,
    ) -> None:
        self.decisions: list[ApprovalDecision] = list(decisions)
        self.requests: list[tuple[ToolCall, PermissionRequest]] = []
        self._on_wait: Callable[[], None] | None = on_wait

    async def wait_approval(self, ctx: RunContext, call: ToolCall, request: PermissionRequest) -> ApprovalDecision:
        self.requests.append((call, request))
        if self._on_wait is not None:
            self._on_wait()
        if not self.decisions:
            # An exhausted script reads as a dismissal, not as a decision nobody
            # sent.
            raise ERR_APPROVAL_DISMISSED
        return self.decisions.pop(0)


class HangingApprovalWaiter:
    """Signals that approval was requested, then waits for cancellation.

    Stands in for a session whose approval channel never delivers: cancelling the
    run context is the only way out, and the engine must treat it as a cancel.
    """

    def __init__(self) -> None:
        self.requested: asyncio.Event = asyncio.Event()

    async def wait_approval(self, ctx: RunContext, call: ToolCall, request: PermissionRequest) -> ApprovalDecision:
        self.requested.set()
        await ctx.done().wait()
        raise Cancelled("approval cancelled")


class LockProbeApprovalStore:
    """An approval store that records whether the engine lock was held.

    :class:`DefaultActionResultResolver` never lets the engine write the store
    while holding the engine lock, and this fake is how the test proves it:
    ``probe`` is the test's ``lambda: engine.lock.locked()``.
    """

    def __init__(self) -> None:
        self.allowed: dict[ApprovalKey, bool] = {}
        self.lock_states: list[bool] = []
        self.probe: Callable[[], bool] | None = None

    def allow_always(self, key: ApprovalKey) -> None:
        if self.probe is not None:
            self.lock_states.append(self.probe())
        self.allowed[key] = True

    def is_always_allowed(self, key: ApprovalKey) -> bool:
        return self.allowed.get(key, False)


class SpecProbeRunner:
    """Answers each action and records whether the lock was held fetching specs.

    Fetching tool specs can block for a long time (an MCP reconnect, say), so the
    engine must do it outside the lock; ``probe`` is the test's
    ``lambda: engine.lock.locked()``.
    """

    def __init__(self, specs: Sequence[ToolSpec] = ()) -> None:
        self.specs: list[ToolSpec] = list(specs)
        self.probe: Callable[[], bool] | None = None
        self.lock_states: list[bool] = []
        self.model_runs: int = 0

    def tool_specs(self) -> list[ToolSpec]:
        if self.probe is not None:
            self.lock_states.append(self.probe())
        return self.specs

    async def run(
        self,
        ctx: RunContext,
        action: QueuedAction,
        input: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ActionCompletion:
        result = self._result_for(action)
        return ActionCompletion(run_id=action.run_id, action_id=action.action_id, result=result)

    def _result_for(self, action: QueuedAction) -> ScheduledActionResult:
        current = action.action
        if isinstance(current, CallModel):
            self.model_runs += 1
            if self.model_runs == 1:
                return ModelReplied(
                    response=ModelResponse(
                        tool_calls=(ToolCall(id="call-1", name="bash", input="pwd"),),
                    )
                )
            return ModelReplied(response=ModelResponse(content="done"))
        if isinstance(current, RunTool):
            return ToolFinished(call=current.call, result="ok")
        if isinstance(current, CheckToolQueue):
            return ToolQueueChecked()
        return ModelReplied(response=ModelResponse(content="done"))
