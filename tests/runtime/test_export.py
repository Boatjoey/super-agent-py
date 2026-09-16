"""Session export in markdown, JSON, and HTML.

The HTML case is a security assertion, not a formatting one: transcript content
must never reach the output as live markup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from super_agent import store, workspace
from super_agent.runtime.engine import NewEngineWithExecutor
from super_agent.runtime.execution import DefaultScheduledActionExecutor
from super_agent.runtime.machine import RoleSystem, RoleUser, ToolCall
from super_agent.runtime.protocol.types import Message
from super_agent.runtime.session import Metadata, NewPersistentSession, SessionID


def configured_workspace(root: str) -> workspace.Workspace:
    return workspace.New(workspace.NewDefaultContext(root))


@pytest.mark.parametrize("format", ["markdown", "json", "html"])
def test_session_exports_markdown_json_and_local_html(tmp_path: Path, format: str) -> None:
    messages = [
        Message(Role=RoleSystem, Content="rules"),
        Message(Role=RoleUser, Content="<hello>"),
    ]
    engine = NewEngineWithExecutor(DefaultScheduledActionExecutor(None, None), messages)
    session = NewPersistentSession(
        engine,
        None,
        configured_workspace(str(tmp_path)),
        Metadata(Title="Demo", Provider="test", Model="model", CWD=str(tmp_path)),
    )

    path = session.Export(format)

    content = Path(path).read_text(encoding="utf-8")
    assert content, f"empty {format} export"
    if format == "html":
        assert "&lt;hello&gt;" in content
        assert "<hello>" not in content


def test_export_writes_under_the_hyphenated_workspace_directory(tmp_path: Path) -> None:
    """The export path spelling is part of the on-disk contract."""
    engine = NewEngineWithExecutor(DefaultScheduledActionExecutor(None, None), [])
    session = NewPersistentSession(
        engine,
        None,
        configured_workspace(str(tmp_path)),
        Metadata(ID=SessionID("session-1"), Title="Demo"),
    )

    path = session.Export("md")

    assert path.endswith(".md")
    assert ".super-agent" in path
    assert Path(path).is_file()


def test_unknown_export_format_is_rejected(tmp_path: Path) -> None:
    engine = NewEngineWithExecutor(DefaultScheduledActionExecutor(None, None), [])
    session = NewPersistentSession(engine, None, configured_workspace(str(tmp_path)), Metadata())

    with pytest.raises(ValueError, match="export format must be markdown, json, or html"):
        session.Export("pdf")


def test_session_export_includes_persisted_audit_events(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    repository_store = store.New(str(root))
    meta = repository_store.Create(store.Metadata(Title="Audit", Provider="test", Model="model", CWD=str(tmp_path)), [])
    call = ToolCall(ID="call-1", Name="run_command", Input='{"command":"ls -la"}')
    repository_store.Append(meta.ID, store.Record(Type=store.EventApprovalDecision, ToolCall=call, Decision="deny"))
    repository_store.Append(meta.ID, store.Record(Type=store.EventError, Error="denied"))

    engine = NewEngineWithExecutor(DefaultScheduledActionExecutor(None, None), [])
    session = NewPersistentSession(
        engine,
        store.NewRepository(repository_store),
        configured_workspace(str(tmp_path)),
        Metadata(ID=SessionID(meta.ID), Title="Audit"),
    )

    content = Path(session.Export("json")).read_text(encoding="utf-8")

    for expected in ('"events"', '"approval_decision"', '"run_command"', '"denied"'):
        assert expected in content, f"export missing {expected}: {content}"
    payload = json.loads(content)
    # session.Metadata's keys are its dataclass field names; the export
    # reproduces that rather than inventing lowercase ones.
    assert payload["metadata"]["Title"] == "Audit"
    assert len(payload["events"]) == 2


def test_export_reports_when_the_workspace_cannot_write() -> None:
    class InertWorkspace:
        def Spec(self) -> object:
            raise NotImplementedError

    engine = NewEngineWithExecutor(DefaultScheduledActionExecutor(None, None), [])
    session = NewPersistentSession(engine, None, InertWorkspace(), Metadata())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="session export is unavailable"):
        session.Export("markdown")
