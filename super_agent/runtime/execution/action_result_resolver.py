"""Map a scheduled action's result onto the event the machine should receive.

This is the only place where "what happened" becomes "what the state machine
learns", which is why every decision about a tool call — deny, run, or ask —
lands here as exactly one of three events.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol

from super_agent.runtime.execution.approval_store import ApprovalStore, new_approval_key
from super_agent.runtime.execution.policy import Policy, ToolDecision, ToolPolicyInput
from super_agent.runtime.execution.scheduled_action_result import (
    ApprovalReceived,
    ModelReplied,
    ScheduledActionResult,
    ToolFinished,
    ToolQueueChecked,
)
from super_agent.runtime.machine.approval import (
    APPROVE_ALWAYS,
    APPROVE_ONCE,
    DENY_APPROVAL,
    ApprovalDecision,
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

    tool_batch: ToolCallBatch | None = None
    tool_specs: tuple[ToolSpec, ...] = ()


class ActionResultResolver(Protocol):
    """Turns an action result into a machine event."""

    def resolve(self, result: ScheduledActionResult, input: ActionResultInput) -> Event: ...


class DefaultActionResultResolver:
    """The only resolver; the port exists so tests can substitute one."""

    __slots__ = ("_approvals", "_policy")

    def __init__(self, policy: Policy, approvals: ApprovalStore) -> None:
        self._policy = policy
        self._approvals = approvals

    def set_policy(self, policy: Policy) -> None:
        """Swap the policy when the permission mode changes mid-session."""
        self._policy = policy

    def resolve(self, result: ScheduledActionResult, input: ActionResultInput) -> Event:
        if isinstance(result, ModelReplied):
            return self._resolve_model_reply(result, input)
        if isinstance(result, ToolFinished):
            return ToolResultReceived(call=result.call, result=result.result)
        if isinstance(result, ToolQueueChecked):
            if input.tool_batch is None or input.tool_batch.index >= len(input.tool_batch.calls):
                return ToolBatchFinished()
            return self.resolveToolCall(input.tool_batch.calls[input.tool_batch.index], input.tool_specs)
        if isinstance(result, ApprovalReceived):
            return self._resolve_approval(result)
        raise ValueError(f"unknown action result type: {type(result).__name__}")

    def resolveToolCall(self, call: ToolCall, specs: tuple[ToolSpec, ...]) -> Event:
        """Classify the next batch call into exactly one of three events."""
        decision = self.decision(call, specs)
        if decision == ToolDecision.DECISION_DENIED:
            request = self._policy.permission_request(call, ToolPolicyInput(tool_specs=specs))
            return ToolCallDenied(call=call, reason=request.reason)
        if decision == ToolDecision.DECISION_RUN_DIRECTLY:
            return ToolCallReadyToRun(call=call)
        return ToolCallNeedsApproval(
            call=call,
            request=self._policy.permission_request(call, ToolPolicyInput(tool_specs=specs)),
        )

    def decision(self, call: ToolCall, specs: tuple[ToolSpec, ...]) -> ToolDecision:
        """An always-allow decision short-circuits classification."""
        if self._approvals.is_always_allowed(new_approval_key(call)):
            return ToolDecision.DECISION_RUN_DIRECTLY
        return self._policy.classify_tool_call(call, ToolPolicyInput(tool_specs=specs))

    def _resolve_model_reply(self, result: ModelReplied, input: ActionResultInput) -> Event:
        if not result.response.tool_calls:
            return AssistantMessageReceived(response=result.response)
        if not input.tool_specs:
            # A model that asks for tools when none are advertised would produce a
            # transcript with an unanswered tool call, which the provider rejects.
            # Failing here answers the turn with an error instead.
            raise ValueError("model returned tool call while tools are disabled")
        return ToolBatchReceived(
            content=result.response.content,
            calls=result.response.tool_calls,
            reasoning_content=result.response.reasoning_content,
        )

    def _resolve_approval(self, result: ApprovalReceived) -> Event:
        decision: ApprovalDecision = result.decision
        if decision == APPROVE_ONCE:
            return ApprovalGranted(call=result.call)
        if decision == APPROVE_ALWAYS:
            return ApprovalAlwaysGranted(call=result.call)
        if decision == DENY_APPROVAL:
            return ApprovalDenied(call=result.call)
        raise ValueError("unknown approval decision")
