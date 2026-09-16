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
    ActionCompletion,
    ApprovalKey,
    ErrApprovalDismissed,
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
        self.specs: list[ToolSpec] = list(specs)
        self.calls: list[ToolCall] = []

    def Specs(self) -> list[ToolSpec]:
        return self.specs

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        self.calls.append(call)
        return self.results.get(call.Name, "")


class RecordingExecutor:
    """One model reply, and it records the actions it was asked to run."""

    def __init__(self) -> None:
        self.actions: list[ScheduledAction] = []

    async def Execute(
        self,
        ctx: RunContext,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ScheduledActionResult:
        self.actions.append(action)
        return ModelReplied(Response=ModelResponse(Content="from executor"))


class FailingOnceExecutor:
    """The first call faults, later ones recover."""

    def __init__(self) -> None:
        self.calls: int = 0
        self.seen: list[Message] = []

    async def Execute(
        self,
        ctx: RunContext,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ScheduledActionResult:
        self.calls += 1
        self.seen = list(env.Messages)
        if self.calls == 1:
            raise RuntimeError("provider timeout")
        return ModelReplied(Response=ModelResponse(Content="recovered"))


class StaticReplyExecutor:
    """Always the same model reply."""

    def __init__(self, content: str = "model summary") -> None:
        self.content: str = content
        self.calls: int = 0

    def ToolSpecs(self) -> list[ToolSpec]:
        return []

    async def Execute(
        self,
        ctx: RunContext,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ScheduledActionResult:
        self.calls += 1
        return ModelReplied(Response=ModelResponse(Content=self.content))


class RecordingPolicy:
    """One fixed decision, and it records its inputs."""

    def __init__(self, decision: ToolDecision) -> None:
        self.decision: ToolDecision = decision
        self.calls: list[ToolCall] = []
        self.specs: list[tuple[ToolSpec, ...]] = []

    def ClassifyToolCall(self, call: ToolCall, input: ToolPolicyInput) -> ToolDecision:
        self.calls.append(call)
        self.specs.append(tuple(input.ToolSpecs))
        return self.decision

    def PermissionRequest(self, call: ToolCall, input: ToolPolicyInput) -> PermissionRequest:
        return PermissionRequest(ToolName=call.Name, Reason="test policy")


class DenyNthPolicy:
    """Run everything directly except the nth call."""

    def __init__(self, deny_at: int) -> None:
        self.deny_at: int = deny_at
        self.seen: int = 0

    def ClassifyToolCall(self, call: ToolCall, input: ToolPolicyInput) -> ToolDecision:
        self.seen += 1
        if self.seen == self.deny_at:
            return ToolDecision.DecisionDenied
        return ToolDecision.DecisionRunDirectly

    def PermissionRequest(self, call: ToolCall, input: ToolPolicyInput) -> PermissionRequest:
        return PermissionRequest(ToolName=call.Name, Reason="denied by test policy")


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

    async def WaitApproval(self, ctx: RunContext, call: ToolCall, request: PermissionRequest) -> ApprovalDecision:
        self.requests.append((call, request))
        if self._on_wait is not None:
            self._on_wait()
        if not self.decisions:
            # An exhausted script reads as a dismissal, not as a decision nobody
            # sent.
            raise ErrApprovalDismissed
        return self.decisions.pop(0)


class HangingApprovalWaiter:
    """Signals that approval was requested, then waits for cancellation.

    Stands in for a session whose approval channel never delivers: cancelling the
    run context is the only way out, and the engine must treat it as a cancel.
    """

    def __init__(self) -> None:
        self.requested: asyncio.Event = asyncio.Event()

    async def WaitApproval(self, ctx: RunContext, call: ToolCall, request: PermissionRequest) -> ApprovalDecision:
        self.requested.set()
        await ctx.Done().wait()
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

    def AllowAlways(self, key: ApprovalKey) -> None:
        if self.probe is not None:
            self.lock_states.append(self.probe())
        self.allowed[key] = True

    def IsAlwaysAllowed(self, key: ApprovalKey) -> bool:
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

    def ToolSpecs(self) -> list[ToolSpec]:
        if self.probe is not None:
            self.lock_states.append(self.probe())
        return self.specs

    async def Run(
        self,
        ctx: RunContext,
        action: QueuedAction,
        input: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ActionCompletion:
        result = self._result_for(action)
        return ActionCompletion(RunID=action.RunID, ActionID=action.ActionID, Result=result)

    def _result_for(self, action: QueuedAction) -> ScheduledActionResult:
        current = action.Action
        if isinstance(current, CallModel):
            self.model_runs += 1
            if self.model_runs == 1:
                return ModelReplied(
                    Response=ModelResponse(
                        ToolCalls=(ToolCall(ID="call-1", Name="bash", Input="pwd"),),
                    )
                )
            return ModelReplied(Response=ModelResponse(Content="done"))
        if isinstance(current, RunTool):
            return ToolFinished(Call=current.Call, Result="ok")
        if isinstance(current, CheckToolQueue):
            return ToolQueueChecked()
        return ModelReplied(Response=ModelResponse(Content="done"))
