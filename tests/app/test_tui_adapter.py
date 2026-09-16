"""Port of ``tests/app/tui_adapter_test.go``.

The adapter is the one place runtime values become presentation values, so the
test drives it through the engine: an unready engine is ``Initializing``, and a
ready one is ``Idle`` with nothing pending.
"""

from __future__ import annotations

import pytest

from super_agent import runtime
from super_agent.app import NewTUIConversation


@pytest.mark.asyncio
async def test_tui_adapter_maps_runtime_state() -> None:
    # Go builds the engine with a nil executor. Here the default executor wraps a
    # nil model and a nil tool runner, which is the same engine for a test that
    # only reads its state.
    engine = runtime.NewEngine(None, None, None)
    conversation = NewTUIConversation(runtime.NewSession(engine))

    status = conversation.Snapshot().AgentStatus
    assert status.Label == "Initializing"
    assert status.Busy is True

    await engine.Ready()

    status = conversation.Snapshot().AgentStatus
    assert status.Label == "Idle"
    assert status.Busy is False
    assert status.AwaitingApproval is False
