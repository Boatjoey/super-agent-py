"""Session export in markdown, JSON, and HTML.

The HTML case is a security assertion, not a formatting one: transcript content
must never reach the output as live markup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from super_agent import store, workspace
from super_agent.runtime.engine import new_engine_with_executor
from super_agent.runtime.execution import DefaultScheduledActionExecutor
from super_agent.runtime.machine import ROLE_SYSTEM, ROLE_USER, ToolCall
from super_agent.runtime.protocol.types import Message
from super_agent.runtime.session import Metadata, SessionID, new_persistent_session


def configured_workspace(root: str) -> workspace.Workspace:
    return workspace.new(workspace.new_default_context(root))


@pytest.mark.parametrize("format", ["markdown", "json", "html"])
def test_session_exports_markdown_json_and_local_html(tmp_path: Path, format: str) -> None:
    messages = [
        Message(role=ROLE_SYSTEM, content="rules"),
        Message(role=ROLE_USER, content="<hello>"),
    ]
    engine = new_engine_with_executor(DefaultScheduledActionExecutor(None, None), messages)
    session = new_persistent_session(
        engine,
        None,
        configured_workspace(str(tmp_path)),
        Metadata(title="Demo", provider="test", model="model", cwd=str(tmp_path)),
    )

    path = session.export(format)

    content = Path(path).read_text(encoding="utf-8")
    assert content, f"empty {format} export"
    if format == "html":
        assert "&lt;hello&gt;" in content
        assert "<hello>" not in content


def test_export_writes_under_the_hyphenated_workspace_directory(tmp_path: Path) -> None:
    """The export path spelling is part of the on-disk contract."""
    engine = new_engine_with_executor(DefaultScheduledActionExecutor(None, None), [])
    session = new_persistent_session(
        engine,
        None,
        configured_workspace(str(tmp_path)),
        Metadata(id=SessionID("session-1"), title="Demo"),
    )

    path = session.export("md")

    assert path.endswith(".md")
    assert ".super-agent" in path
    assert Path(path).is_file()


def test_unknown_export_format_is_rejected(tmp_path: Path) -> None:
    engine = new_engine_with_executor(DefaultScheduledActionExecutor(None, None), [])
    session = new_persistent_session(engine, None, configured_workspace(str(tmp_path)), Metadata())

    with pytest.raises(ValueError, match="export format must be markdown, json, or html"):
        session.export("pdf")


def test_session_export_includes_persisted_audit_events(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    repository_store = store.new(str(root))
    meta = repository_store.create(store.Metadata(title="Audit", provider="test", model="model", cwd=str(tmp_path)), [])
    call = ToolCall(id="call-1", name="run_command", input='{"command":"ls -la"}')
    repository_store.append(meta.id, store.Record(type=store.EVENT_APPROVAL_DECISION, tool_call=call, decision="deny"))
    repository_store.append(meta.id, store.Record(type=store.EVENT_ERROR, error="denied"))

    engine = new_engine_with_executor(DefaultScheduledActionExecutor(None, None), [])
    session = new_persistent_session(
        engine,
        store.new_repository(repository_store),
        configured_workspace(str(tmp_path)),
        Metadata(id=SessionID(meta.id), title="Audit"),
    )

    content = Path(session.export("json")).read_text(encoding="utf-8")

    for expected in ('"events"', '"approval_decision"', '"run_command"', '"denied"'):
        assert expected in content, f"export missing {expected}: {content}"
    payload = json.loads(content)
    # session.Metadata's keys are its dataclass field names, snake_case as the
    # export writes them.
    assert payload["metadata"]["title"] == "Audit"
    assert len(payload["events"]) == 2


def test_export_reports_when_the_workspace_cannot_write() -> None:
    class InertWorkspace:
        def spec(self) -> object:
            raise NotImplementedError

    engine = new_engine_with_executor(DefaultScheduledActionExecutor(None, None), [])
    session = new_persistent_session(engine, None, InertWorkspace(), Metadata())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="session export is unavailable"):
        session.export("markdown")
