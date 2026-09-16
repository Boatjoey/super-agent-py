"""Map a scheduled action's result onto the event the machine should receive.

This is the only place where "what happened" becomes "what the state machine
learns", which is why every decision about a tool call — deny, run, or ask —
lands here as exactly one of three events.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol

from super_agent.runtime.execution.approval_store import ApprovalStore, NewApprovalKey
from super_agent.runtime.execution.policy import Policy, ToolDecision, ToolPolicyInput
from super_agent.runtime.execution.scheduled_action_result import (
    ApprovalReceived,
    ModelReplied,
    ScheduledActionResult,
    ToolFinished,
    ToolQueueChecked,
)
from super_agent.runtime.machine.approval import (
    ApprovalDecision,
    ApproveAlways,
    ApproveOnce,
    DenyApproval,
)
from super_agent.runtime.machine.event import (
    ApprovalAlwaysGranted,
    ApprovalDenied,
    ApprovalGranted,
    AssistantMessageReceived,
    Event,
    ToolBatchFinished,
    ToolBatchReceived,
    ToolCallDenied,
    ToolCallNeedsApproval,
    ToolCallReadyToRun,
    ToolResultReceived,
)
from super_agent.runtime.machine.tool_batch import ToolCallBatch
from super_agent.runtime.protocol.types import ToolCall, ToolSpec


@dataclasses.dataclass(frozen=True, slots=True)
class ActionResultInput:
    """Machine state the resolver needs to interpret a result."""

    ToolBatch: ToolCallBatch | None = None
    ToolSpecs: tuple[ToolSpec, ...] = ()


class ActionResultResolver(Protocol):
    """Turns an action result into a machine event."""

    def Resolve(self, result: ScheduledActionResult, input: ActionResultInput) -> Event: ...


class DefaultActionResultResolver:
    """The only resolver; the port exists so tests can substitute one."""

    __slots__ = ("_approvals", "_policy")

    def __init__(self, policy: Policy, approvals: ApprovalStore) -> None:
        self._policy = policy
        self._approvals = approvals

    def SetPolicy(self, policy: Policy) -> None:
        """Swap the policy when the permission mode changes mid-session."""
        self._policy = policy

    def Resolve(self, result: ScheduledActionResult, input: ActionResultInput) -> Event:
        if isinstance(result, ModelReplied):
            return self._resolve_model_reply(result, input)
        if isinstance(result, ToolFinished):
            return ToolResultReceived(Call=result.Call, Result=result.Result)
        if isinstance(result, ToolQueueChecked):
            if input.ToolBatch is None or input.ToolBatch.Index >= len(input.ToolBatch.Calls):
                return ToolBatchFinished()
            return self.resolveToolCall(input.ToolBatch.Calls[input.ToolBatch.Index], input.ToolSpecs)
        if isinstance(result, ApprovalReceived):
            return self._resolve_approval(result)
        raise ValueError(f"unknown action result type: {type(result).__name__}")

    def resolveToolCall(self, call: ToolCall, specs: tuple[ToolSpec, ...]) -> Event:
        """Classify the next batch call into exactly one of three events."""
        decision = self.decision(call, specs)
        if decision == ToolDecision.DecisionDenied:
            request = self._policy.PermissionRequest(call, ToolPolicyInput(ToolSpecs=specs))
            return ToolCallDenied(Call=call, Reason=request.Reason)
        if decision == ToolDecision.DecisionRunDirectly:
            return ToolCallReadyToRun(Call=call)
        return ToolCallNeedsApproval(
            Call=call,
            Request=self._policy.PermissionRequest(call, ToolPolicyInput(ToolSpecs=specs)),
        )

    def decision(self, call: ToolCall, specs: tuple[ToolSpec, ...]) -> ToolDecision:
        """An always-allow decision short-circuits classification."""
        if self._approvals.IsAlwaysAllowed(NewApprovalKey(call)):
            return ToolDecision.DecisionRunDirectly
        return self._policy.ClassifyToolCall(call, ToolPolicyInput(ToolSpecs=specs))

    def _resolve_model_reply(self, result: ModelReplied, input: ActionResultInput) -> Event:
        if not result.Response.ToolCalls:
            return AssistantMessageReceived(Response=result.Response)
        if not input.ToolSpecs:
            # A model that asks for tools when none are advertised would produce a
            # transcript with an unanswered tool call, which the provider rejects.
            # Failing here answers the turn with an error instead.
            raise ValueError("model returned tool call while tools are disabled")
        return ToolBatchReceived(
            Content=result.Response.Content,
            Calls=result.Response.ToolCalls,
            ReasoningContent=result.Response.ReasoningContent,
        )

    def _resolve_approval(self, result: ApprovalReceived) -> Event:
        decision: ApprovalDecision = result.Decision
        if decision == ApproveOnce:
            return ApprovalGranted(Call=result.Call)
        if decision == ApproveAlways:
            return ApprovalAlwaysGranted(Call=result.Call)
        if decision == DenyApproval:
            return ApprovalDenied(Call=result.Call)
        raise ValueError("unknown approval decision")
