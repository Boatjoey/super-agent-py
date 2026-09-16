"""Telemetry correlation: one turn's records carry one run and action identity.

The sink is a process-global JSONL file, so the test configures it against
``tmp_path`` and always closes it, even when the turn fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from super_agent.runtime import machine
from super_agent.runtime.engine import NewEngineWithExecutor
from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.telemetry import telemetry
from tests.fakes.execution import StaticReplyExecutor


@pytest.mark.asyncio
async def test_engine_writes_correlated_telemetry(tmp_path: Path) -> None:
    path = tmp_path / "telemetry.jsonl"
    telemetry.Configure(str(path))
    try:
        engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
        await engine.Ready()
        await engine.RunTurn(LiveContext(), machine.UserMessageSubmitted(Content="hello"), None, None)
    finally:
        telemetry.Close()

    log = path.read_text()
    for expected in (
        '"kind":"transition"',
        '"kind":"action"',
        '"kind":"run"',
        '"run_id":"run-1"',
        '"action_id":"action-1"',
        '"component":"model"',
        '"input_tokens_estimate"',
        '"output_tokens_estimate"',
    ):
        assert expected in log, log
