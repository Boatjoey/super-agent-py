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
    ERR_APPROVAL_DISMISSED,
    ApprovalReceived,
    DefaultScheduledActionExecutor,
    ScheduledActionInput,
)
from super_agent.runtime.protocol.run_context import live_context
from super_agent.runtime.protocol.types import ToolCall


class RecordingWaiter:
    """An approval waiter that records what it was asked and answers with a fixed decision."""

    def __init__(self, decision: machine.ApprovalDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[ToolCall, machine.PermissionRequest]] = []

    async def wait_approval(
        self,
        ctx: object,
        call: ToolCall,
        request: machine.PermissionRequest,
    ) -> machine.ApprovalDecision:
        self.calls.append((call, request))
        return self.decision


class DismissingWaiter:
    async def wait_approval(
        self,
        ctx: object,
        call: ToolCall,
        request: machine.PermissionRequest,
    ) -> machine.ApprovalDecision:
        raise ERR_APPROVAL_DISMISSED


@pytest.mark.asyncio
async def test_await_approval_action_uses_injected_waiter() -> None:
    call = ToolCall(id="call-1", name="bash")
    request = machine.PermissionRequest(tool_name="bash", reason="risky")
    waiter = RecordingWaiter(machine.APPROVE_ALWAYS)
    executor = DefaultScheduledActionExecutor(None, None)

    result = await executor.execute(
        live_context(),
        machine.AwaitApproval(call=call, request=request),
        ScheduledActionInput(approval_waiter=waiter),
        lambda _chunk: None,
    )

    assert isinstance(result, ApprovalReceived)
    assert result.call == call
    assert result.decision == machine.APPROVE_ALWAYS
    assert waiter.calls == [(call, request)]


@pytest.mark.asyncio
async def test_await_approval_without_a_waiter_is_a_fault() -> None:
    """No waiter means the session is misconfigured, not that the user said no."""
    executor = DefaultScheduledActionExecutor(None, None)
    with pytest.raises(ValueError, match="approval waiter is not configured"):
        await executor.execute(
            live_context(),
            machine.AwaitApproval(call=ToolCall(id="call-1", name="bash")),
            ScheduledActionInput(),
            lambda _chunk: None,
        )


@pytest.mark.asyncio
async def test_approval_dismissal_is_reported_as_the_sentinel() -> None:
    """The engine cancels on dismissal, so the sentinel must survive identically.

    ``ERR_APPROVAL_DISMISSED`` is a single instance: the engine compares against it
    by identity rather than by type or message.
    """
    executor = DefaultScheduledActionExecutor(None, None)
    with pytest.raises(Exception) as raised:
        await executor.execute(
            live_context(),
            machine.AwaitApproval(call=ToolCall(id="call-1", name="bash")),
            ScheduledActionInput(approval_waiter=DismissingWaiter()),
            lambda _chunk: None,
        )

    assert errors_is(raised.value, ERR_APPROVAL_DISMISSED)
    assert errors_is(raised.value, Cancelled)


@pytest.mark.asyncio
async def test_a_cancelled_waiter_propagates_as_cancellation_not_an_error() -> None:
    """Cancellation must not be reported to the model as a tool failure."""

    class CancellingWaiter:
        async def wait_approval(
            self, ctx: object, call: ToolCall, request: machine.PermissionRequest
        ) -> machine.ApprovalDecision:
            raise asyncio.CancelledError

    executor = DefaultScheduledActionExecutor(None, None)
    with pytest.raises(asyncio.CancelledError):
        await executor.execute(
            live_context(),
            machine.AwaitApproval(call=ToolCall(id="call-1", name="bash")),
            ScheduledActionInput(approval_waiter=CancellingWaiter()),
            lambda _chunk: None,
        )
