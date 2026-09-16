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
from super_agent.runtime.engine import NewEngineWithExecutor
from super_agent.runtime.execution import (
    DefaultScheduledActionExecutor,
    ModelReplied,
    ScheduledActionInput,
)
from super_agent.runtime.machine import ModelResponse, ScheduledAction
from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import ToolSpec
from super_agent.runtime.session import Metadata, NewPersistentSession

NOTIFICATION_CAPACITY = 20


class _StaticExecutor:
    """Answers every model call with the same reply.

    Local to this module so it fails on its own terms; ``test_session_store.py``
    has a recording version of the same idea.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def Execute(
        self,
        ctx: object,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: object,
    ) -> ModelReplied:
        self.calls += 1
        return ModelReplied(Response=ModelResponse(Content="model summary"))

    def ToolSpecs(self) -> list[ToolSpec]:
        return []


def configured_workspace(root: str) -> workspace.Workspace:
    """A workspace rooted at ``root``, which is what the session's ports expect."""
    return workspace.New(workspace.NewDefaultContext(root))


@pytest.mark.asyncio
async def test_attached_file_is_consumed_by_next_user_message(tmp_path: Path) -> None:
    (tmp_path / "note.txt").write_text("attachment body", encoding="utf-8")
    engine = NewEngineWithExecutor(_StaticExecutor(), [])
    await engine.Ready()
    session = NewPersistentSession(engine, None, configured_workspace(str(tmp_path)), Metadata())

    attachment = session.Attach("note.txt")

    assert attachment.Name == "note.txt"
    assert attachment.Data == base64.b64encode(b"attachment body").decode()

    notifications: asyncio.Queue[object] = asyncio.Queue(maxsize=NOTIFICATION_CAPACITY)
    approvals: asyncio.Queue[object] = asyncio.Queue()
    await session.RunTurn(LiveContext(), "inspect", notifications, approvals)  # type: ignore[arg-type]

    messages = session.Snapshot().Messages
    assert len(messages) >= 1
    assert len(messages[0].Attachments) == 1
    assert messages[0].Attachments[0].Name == "note.txt"
    assert session.PendingAttachments() == []


def test_attachment_cannot_escape_workspace(tmp_path: Path) -> None:
    engine = NewEngineWithExecutor(DefaultScheduledActionExecutor(None, None), [])
    session = NewPersistentSession(engine, None, configured_workspace(str(tmp_path)), Metadata())

    with pytest.raises(Exception):  # noqa: B017 - the workspace decides the error type
        session.Attach("../outside.txt")


def test_attachment_staging_is_reported_when_unavailable() -> None:
    """A session with a workspace that cannot read files says so plainly."""

    class InertWorkspace:
        def Spec(self) -> object:
            raise NotImplementedError

    engine = NewEngineWithExecutor(DefaultScheduledActionExecutor(None, None), [])
    session = NewPersistentSession(engine, None, InertWorkspace(), Metadata())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="attachments are unavailable"):
        session.Attach("note.txt")
