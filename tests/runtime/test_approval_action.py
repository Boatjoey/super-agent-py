"""The approval scheduled action and its waiter.

The cases here pin the dismissal sentinel: approval travels the same action →
result → event path as a model call, which is why the session needs no scheduling
authority of its own.
"""

from __future__ import annotations

import asyncio

import pytest

from super_agent.errors import Cancelled, errors_is
from super_agent.runtime import machine
from super_agent.runtime.execution import (
    ApprovalReceived,
    DefaultScheduledActionExecutor,
    ErrApprovalDismissed,
    ScheduledActionInput,
)
from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import ToolCall


class RecordingWaiter:
    """An approval waiter that records what it was asked and answers with a fixed decision."""

    def __init__(self, decision: machine.ApprovalDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[ToolCall, machine.PermissionRequest]] = []

    async def WaitApproval(
        self,
        ctx: object,
        call: ToolCall,
        request: machine.PermissionRequest,
    ) -> machine.ApprovalDecision:
        self.calls.append((call, request))
        return self.decision


class DismissingWaiter:
    async def WaitApproval(
        self,
        ctx: object,
        call: ToolCall,
        request: machine.PermissionRequest,
    ) -> machine.ApprovalDecision:
        raise ErrApprovalDismissed


@pytest.mark.asyncio
async def test_await_approval_action_uses_injected_waiter() -> None:
    call = ToolCall(ID="call-1", Name="bash")
    request = machine.PermissionRequest(ToolName="bash", Reason="risky")
    waiter = RecordingWaiter(machine.ApproveAlways)
    executor = DefaultScheduledActionExecutor(None, None)

    result = await executor.Execute(
        LiveContext(),
        machine.AwaitApproval(Call=call, Request=request),
        ScheduledActionInput(ApprovalWaiter=waiter),
        lambda _chunk: None,
    )

    assert isinstance(result, ApprovalReceived)
    assert result.Call == call
    assert result.Decision == machine.ApproveAlways
    assert waiter.calls == [(call, request)]


@pytest.mark.asyncio
async def test_await_approval_without_a_waiter_is_a_fault() -> None:
    """No waiter means the session is misconfigured, not that the user said no."""
    executor = DefaultScheduledActionExecutor(None, None)
    with pytest.raises(ValueError, match="approval waiter is not configured"):
        await executor.Execute(
            LiveContext(),
            machine.AwaitApproval(Call=ToolCall(ID="call-1", Name="bash")),
            ScheduledActionInput(),
            lambda _chunk: None,
        )


@pytest.mark.asyncio
async def test_approval_dismissal_is_reported_as_the_sentinel() -> None:
    """The engine cancels on dismissal, so the sentinel must survive identically.

    ``ErrApprovalDismissed`` is a single instance: the engine compares against it
    by identity rather than by type or message.
    """
    executor = DefaultScheduledActionExecutor(None, None)
    with pytest.raises(Exception) as raised:
        await executor.Execute(
            LiveContext(),
            machine.AwaitApproval(Call=ToolCall(ID="call-1", Name="bash")),
            ScheduledActionInput(ApprovalWaiter=DismissingWaiter()),
            lambda _chunk: None,
        )

    assert errors_is(raised.value, ErrApprovalDismissed)
    assert errors_is(raised.value, Cancelled)


@pytest.mark.asyncio
async def test_a_cancelled_waiter_propagates_as_cancellation_not_an_error() -> None:
    """Cancellation must not be reported to the model as a tool failure."""

    class CancellingWaiter:
        async def WaitApproval(
            self, ctx: object, call: ToolCall, request: machine.PermissionRequest
        ) -> machine.ApprovalDecision:
            raise asyncio.CancelledError

    executor = DefaultScheduledActionExecutor(None, None)
    with pytest.raises(asyncio.CancelledError):
        await executor.Execute(
            LiveContext(),
            machine.AwaitApproval(Call=ToolCall(ID="call-1", Name="bash")),
            ScheduledActionInput(ApprovalWaiter=CancellingWaiter()),
            lambda _chunk: None,
        )
