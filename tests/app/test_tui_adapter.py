"""The TUI adapter's mapping of runtime values to presentation values.

The adapter is the one place runtime values become presentation values, so the
test drives it through the engine: an unready engine is ``Initializing``, and a
ready one is ``Idle`` with nothing pending.
"""

from __future__ import annotations

import pytest

from super_agent import runtime
from super_agent.app import new_tui_conversation


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
