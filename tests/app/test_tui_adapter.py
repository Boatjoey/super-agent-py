"""The TUI adapter's mapping of runtime values to presentation values.

The adapter is the one place runtime values become presentation values, so the
test drives it through the engine: an unready engine is ``Initializing``, and a
ready one is ``Idle`` with nothing pending.
"""

from __future__ import annotations

import pytest

from super_agent import runtime, tui
from super_agent.app import new_tui_conversation
from super_agent.app.tui_adapter import to_conversation_notification
from tests.fakes.model import ScriptedModel


@pytest.mark.asyncio
async def test_tui_adapter_maps_runtime_state() -> None:
    # The engine is built with nil ports: the default executor wraps a nil model
    # and a nil tool runner, which is the same engine for a test that only reads
    # its state.
    engine = runtime.new_engine(None, None, None)
    conversation = new_tui_conversation(runtime.new_session(engine))

    status = conversation.snapshot().agent_status
    assert status.label == "Initializing"
    assert status.busy is True

    await engine.ready()

    status = conversation.snapshot().agent_status
    assert status.label == "Idle"
    assert status.busy is False
    assert status.awaiting_approval is False


def test_tui_adapter_maps_reported_usage() -> None:
    """The provider's counts cross the boundary unchanged, as the TUI's own DTO."""
    notification = to_conversation_notification(
        runtime.UsageReported(usage=runtime.Usage(input_tokens=11, output_tokens=7, total_tokens=18))
    )

    assert notification == tui.UsageReported(usage=tui.ContextUsage(input_tokens=11, output_tokens=7, total_tokens=18))


@pytest.mark.asyncio
async def test_tui_adapter_delivers_provider_usage_through_a_turn() -> None:
    """A reported usage reaches the interface while the turn runs.

    The whole path is exercised — engine, session notification, adapter, channel —
    because a mapping that is never reached is not a working notification.
    """
    model = ScriptedModel(
        [
            runtime.ModelResponse(
                content="hello",
                usage=runtime.Usage(input_tokens=11, output_tokens=7, total_tokens=18),
            )
        ]
    )
    engine = runtime.new_engine(model, None, None)
    await engine.ready()
    conversation = new_tui_conversation(runtime.new_session(engine))
    notifications: tui.Channel[tui.ConversationNotification] = tui.Channel()
    approvals: tui.Channel[tui.ApprovalDecision] = tui.Channel()

    error = await conversation.run_turn("hi", notifications, approvals, tui.Cancellation())
    assert error is None

    seen: list[tui.ConversationNotification] = []
    while (notification := await notifications.get()) is not None:
        seen.append(notification)

    assert [item for item in seen if isinstance(item, tui.UsageReported)] == [
        tui.UsageReported(usage=tui.ContextUsage(input_tokens=11, output_tokens=7, total_tokens=18))
    ]
