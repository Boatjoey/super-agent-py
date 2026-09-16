"""Attachments staged for the next turn.

Attachment staging is a narrow optional port on the workspace, so both cases here
run against the real workspace implementation rather than a stand-in: the escape
check is a security property and testing it against a fake would prove nothing.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from super_agent import workspace
from super_agent.runtime.engine import new_engine_with_executor
from super_agent.runtime.execution import (
    DefaultScheduledActionExecutor,
    ModelReplied,
    ScheduledActionInput,
)
from super_agent.runtime.machine import ModelResponse, ScheduledAction
from super_agent.runtime.protocol.run_context import live_context
from super_agent.runtime.protocol.types import ToolSpec
from super_agent.runtime.session import Metadata, new_persistent_session

NOTIFICATION_CAPACITY = 20


class _StaticExecutor:
    """Answers every model call with the same reply.

    Local to this module so it fails on its own terms; ``test_session_store.py``
    has a recording version of the same idea.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        ctx: object,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: object,
    ) -> ModelReplied:
        self.calls += 1
        return ModelReplied(response=ModelResponse(content="model summary"))

    def tool_specs(self) -> list[ToolSpec]:
        return []


def configured_workspace(root: str) -> workspace.Workspace:
    """A workspace rooted at ``root``, which is what the session's ports expect."""
    return workspace.new(workspace.new_default_context(root))


@pytest.mark.asyncio
async def test_attached_file_is_consumed_by_next_user_message(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("attachment body", encoding="utf-8")
    engine = new_engine_with_executor(_StaticExecutor(), [])
    await engine.ready()
    session = new_persistent_session(engine, None, configured_workspace(str(tmp_path)), Metadata())

    attachment = session.attach("note.txt")

    assert attachment.name == "note.txt"
    assert attachment.data == base64.b64encode(b"attachment body").decode()

    notifications: asyncio.Queue[object] = asyncio.Queue(maxsize=NOTIFICATION_CAPACITY)
    approvals: asyncio.Queue[object] = asyncio.Queue()
    await session.run_turn(live_context(), "inspect", notifications, approvals)  # type: ignore[arg-type]

    messages = session.snapshot().messages
    assert len(messages) >= 1
    assert len(messages[0].attachments) == 1
    assert messages[0].attachments[0].name == "note.txt"
    assert session.pending_attachments() == []


def test_attachment_cannot_escape_workspace(tmp_path: Path) -> None:
    engine = new_engine_with_executor(DefaultScheduledActionExecutor(None, None), [])
    session = new_persistent_session(engine, None, configured_workspace(str(tmp_path)), Metadata())

    with pytest.raises(Exception):  # noqa: B017 - the workspace decides the error type
        session.attach("../outside.txt")


def test_attachment_staging_is_reported_when_unavailable() -> None:
    """A session with a workspace that cannot read files says so plainly."""

    class InertWorkspace:
        def spec(self) -> object:
            raise NotImplementedError

    engine = new_engine_with_executor(DefaultScheduledActionExecutor(None, None), [])
    session = new_persistent_session(engine, None, InertWorkspace(), Metadata())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="attachments are unavailable"):
        session.attach("note.txt")
