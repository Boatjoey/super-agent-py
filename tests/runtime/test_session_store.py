"""The store, and the session use cases that run over it.

Two cases whose subject is engine behaviour rather than storage already live in
``test_engine.py``:

* the engine half of "a concurrent run turn fails without blocking events" is
  ``test_invalid_second_turn_does_not_cancel_active_run``; the session half, the
  busy error, is ``test_session_run_turn_refuses_a_second_turn`` below.
* ``test_no_tools_tool_call_is_protocol_error``.

The store-only cases are at the end: replay of a checked-in golden store, the
torn tail and mid-file corruption pairs, the 20 MiB read cap, identifiers, and
atomic metadata writes.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import re
import shutil
import stat
import sys
import threading
from pathlib import Path

import pytest

from super_agent import store, workspace
from super_agent.runtime import machine
from super_agent.runtime.engine import Engine, new_engine, new_engine_with_executor
from super_agent.runtime.protocol.run_context import live_context
from super_agent.runtime.protocol.types import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolCall,
)
from super_agent.runtime.session import (
    WORKSPACE_ACCESS_READ_WRITE,
    ApprovalsClosed,
    FileSnapshot,
    Metadata,
    NotificationsClosed,
    Session,
    SessionID,
    SessionNotification,
    WorkspaceRootSpec,
    WorkspaceSpec,
    new_persistent_session,
    new_session,
)
from tests.fakes.execution import FakeToolRunner, StaticReplyExecutor
from tests.fakes.model import BlockingModel

#: A checked-in golden session store. Read-only here; every test that mutates it
#: copies it to ``tmp_path`` first.
SESSION_STORE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "session_store"

#: A session whose stored id does not matter yet; the explicit conversion keeps
#: the value a `SessionID`.
NEW_ID = store.SessionID("new")


# --- helpers -----------------------------------------------------------------


def configured_workspace(root: str) -> workspace.Workspace:
    """The workspace helper the session tests share."""
    return workspace.new(workspace.new_default_context(root))


def persistent_session(engine: Engine, st: store.Store, meta: store.Metadata) -> Session:
    """The persistent-session helper the store tests share."""
    root = meta.cwd or os.getcwd()
    return new_persistent_session(
        engine,
        store.new_repository(st),
        configured_workspace(root),
        Metadata(
            id=SessionID(meta.id),
            title=meta.title,
            provider=meta.provider,
            model=meta.model,
            cwd=meta.cwd,
            instruction_sources=tuple(meta.instruction_sources),
        ),
    )


def new_queues() -> tuple[
    asyncio.Queue[SessionNotification], asyncio.Queue[machine.ApprovalDecision | ApprovalsClosed]
]:
    notifications: asyncio.Queue[SessionNotification] = asyncio.Queue()
    approvals: asyncio.Queue[machine.ApprovalDecision | ApprovalsClosed] = asyncio.Queue()
    return notifications, approvals


async def run_turn(session: Session, query: str) -> None:
    """Run one turn with no approvals needed and nobody watching the queue."""
    notifications, approvals = new_queues()
    await session.run_turn(live_context(), query, notifications, approvals)


class CheckpointWorkspace:
    """A port stub that records what it captured."""

    def __init__(self, files: list[FileSnapshot]) -> None:
        self.paths: list[str] = []
        self.files: list[FileSnapshot] = files

    def spec(self) -> WorkspaceSpec:
        return WorkspaceSpec(
            primary_root="/work",
            cwd="/work",
            roots=(WorkspaceRootSpec(path="/work", access=WORKSPACE_ACCESS_READ_WRITE),),
        )

    def validate(self, spec: WorkspaceSpec) -> None:
        return None

    def canonicalize(self, spec: WorkspaceSpec) -> WorkspaceSpec:
        return spec

    def activate(self, spec: WorkspaceSpec) -> None:
        return None

    def capture(self, paths: list[str]) -> list[FileSnapshot]:
        self.paths = list(paths)
        return self.files

    def restore(self, files: list[FileSnapshot]) -> None:
        return None


class FailingWorkspaceRepository(store.Repository):
    """A repository whose migration write always fails."""

    def save_workspace_description(self, session_id: SessionID, spec: WorkspaceSpec) -> None:
        raise RuntimeError("workspace metadata write failed")


class CanonicalizeSpy(workspace.Workspace):
    """Counts how often the legacy workspace upgrade ran."""

    def __init__(self, context: workspace.Context) -> None:
        super().__init__(context)
        self.calls = 0

    def canonicalize(self, spec: WorkspaceSpec) -> WorkspaceSpec:
        self.calls += 1
        return super().canonicalize(spec)


# --- session over the store --------------------------------------------------


@pytest.mark.asyncio
async def test_persistent_session_resumes_conversation_with_tool_results(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    initial = [Message(role=ROLE_SYSTEM, content="rules")]
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(cwd)), initial)
    st.append(meta.id, store.Record(type=store.EVENT_MESSAGE_APPENDED, message=Message(role=ROLE_USER, content="hi")))
    call = ToolCall(id="call-1", name="read_file")
    st.append(meta.id, store.Record(type=store.EVENT_TOOL_RESULT, tool_call=call, result="file contents"))

    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(id=NEW_ID))
    await session.resume(SessionID(meta.id))

    messages = session.snapshot().messages
    assert len(messages) == 3
    assert messages[2].role == ROLE_TOOL
    assert messages[2].content == "file contents"
    assert messages[2].tool_call_id == "call-1"


@pytest.mark.asyncio
async def test_resume_migrates_legacy_workspace_to_canonical_spec(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    legacy_cwd = str(tmp_path / "legacy")
    os.mkdir(legacy_cwd)
    meta = st.create(
        store.Metadata(
            title="legacy",
            provider="test",
            model="test-model",
            cwd=legacy_cwd,
            instruction_sources=("AGENTS.md",),
        ),
        [Message(role=ROLE_SYSTEM, content="rules")],
    )
    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(id=NEW_ID))
    await session.resume(SessionID(meta.id))

    canonical = os.path.realpath(legacy_cwd)
    persisted = st.metadata(meta.id)
    assert persisted.workspace is not None
    assert persisted.workspace.primary_root == canonical
    assert canonical == persisted.workspace.cwd
    assert canonical == persisted.cwd
    assert len(persisted.workspace.roots) == 1
    assert persisted.workspace.roots[0].path == canonical
    assert persisted.workspace.roots[0].access == "read_write"
    # Migration touches only the workspace description: unrelated metadata,
    # including ProjectID and ConfigRoot, keeps its saved value.
    assert persisted.title == "legacy"
    assert persisted.provider == "test"
    assert persisted.model == "test-model"
    assert persisted.created_at == meta.created_at
    assert persisted.instruction_sources == ("AGENTS.md",)
    assert persisted.project_id == ""
    assert persisted.config_root == ""


@pytest.mark.asyncio
async def test_resume_legacy_workspace_migration_is_idempotent(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    legacy_cwd = str(tmp_path / "legacy")
    os.mkdir(legacy_cwd)
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=legacy_cwd), [])
    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(id=NEW_ID))
    await session.resume(SessionID(meta.id))
    first = st.metadata(meta.id)
    assert first.workspace is not None

    await session.resume(SessionID(meta.id))
    second = st.metadata(meta.id)
    assert first.workspace == second.workspace


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="symlinks commonly need elevated privileges on Windows")
async def test_resume_uses_strict_validation_after_legacy_migration(tmp_path: Path) -> None:
    saved_root = tmp_path / "project"
    saved_root.mkdir()
    st = store.new(str(tmp_path / "store"))
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(saved_root)), [])
    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(id=NEW_ID))
    await session.resume(SessionID(meta.id))

    # The one-time upgrade persisted the canonical root, so replacing it with an
    # escaping symlink must now fail the strict check instead of silently
    # adopting the new target.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.rmdir(saved_root)
    os.symlink(elsewhere, saved_root)
    with pytest.raises(ValueError, match="saved workspace is no longer valid"):
        await session.resume(SessionID(meta.id))


@pytest.mark.asyncio
async def test_resume_does_not_fall_back_to_legacy_when_saved_spec_is_invalid(tmp_path: Path) -> None:
    valid_cwd = str(tmp_path / "valid")
    os.mkdir(valid_cwd)
    missing = str(tmp_path / "missing")
    st = store.new(str(tmp_path / "store"))
    spec = WorkspaceSpec(
        primary_root=missing,
        cwd=missing,
        roots=(WorkspaceRootSpec(path=missing, access=WORKSPACE_ACCESS_READ_WRITE),),
    )
    meta = store.new_repository(st).create(
        Metadata(provider="test", model="test-model", cwd=valid_cwd, workspace_spec=spec),
        [],
    )
    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(id=NEW_ID))
    with pytest.raises(ValueError, match="saved workspace is no longer valid"):
        await session.resume(SessionID(meta.id))

    persisted = st.metadata(store.SessionID(meta.id))
    assert persisted.workspace is not None
    assert persisted.workspace.primary_root == missing


@pytest.mark.asyncio
async def test_resume_fails_when_workspace_migration_cannot_be_persisted(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    legacy_cwd = str(tmp_path / "legacy")
    os.mkdir(legacy_cwd)
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=legacy_cwd), [])
    repository = FailingWorkspaceRepository(st)
    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    session = new_persistent_session(
        engine, repository, configured_workspace(str(tmp_path)), Metadata(id=SessionID("new"))
    )
    with pytest.raises(ValueError, match="persist migrated workspace"):
        await session.resume(SessionID(meta.id))

    persisted = st.metadata(meta.id)
    assert persisted.workspace is None


@pytest.mark.asyncio
async def test_resume_upgrades_legacy_workspace_only_once(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    legacy_cwd = str(tmp_path / "legacy")
    os.mkdir(legacy_cwd)
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=legacy_cwd), [])
    spy = CanonicalizeSpy(workspace.new_default_context(str(tmp_path)))
    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    session = new_persistent_session(engine, store.new_repository(st), spy, Metadata(id=SessionID("new")))

    await session.resume(SessionID(meta.id))
    assert spy.calls == 1
    await session.resume(SessionID(meta.id))
    assert spy.calls == 1


def test_session_list_preserves_parent_relationship(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    repository = store.new_repository(st)
    created = repository.create(Metadata(parent_id=SessionID("parent"), provider="test", model="model"), [])

    items = repository.list()

    assert len(items) == 1
    assert items[0].id == created.id
    assert items[0].parent_id == "parent"


@pytest.mark.asyncio
async def test_session_fork_copies_transcript_and_selects_child(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    initial = [Message(role=ROLE_SYSTEM, content="rules")]
    meta = st.create(store.Metadata(title="parent", provider="test", model="model", cwd=str(tmp_path)), initial)
    engine = new_engine_with_executor(StaticReplyExecutor(), initial)
    await engine.ready()
    session = persistent_session(engine, st, meta)

    child = await session.fork("experiment")

    assert child.parent_id == SessionID(meta.id)
    assert session.metadata().id == child.id
    messages, _meta = store.new_repository(st).load(child.id)
    assert len(messages) == 1
    assert messages[0].content == "rules"


@pytest.mark.asyncio
async def test_cross_session_memory_updates_transcript(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    initial = [Message(role=ROLE_SYSTEM, content="rules"), Message(role=ROLE_USER, content="hello")]
    meta = st.create(store.Metadata(title="memory", cwd=str(tmp_path)), initial)
    engine = new_engine_with_executor(StaticReplyExecutor(), initial)
    await engine.ready()
    session = persistent_session(engine, st, meta)

    await session.remember("Prefer concise answers")
    assert session.memories() == ["Prefer concise answers"]

    messages = session.snapshot().messages
    assert len(messages) == 3
    assert "Prefer concise answers" in messages[0].content
    assert messages[2].content == "hello"

    await session.forget_memories()
    assert len(session.snapshot().messages) == 2


@pytest.mark.asyncio
async def test_persistent_reset_preserves_system_messages(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    initial = [Message(role=ROLE_SYSTEM, content="rules")]
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(tmp_path)), initial)
    engine = new_engine_with_executor(StaticReplyExecutor(), initial)
    await engine.ready()
    session = persistent_session(engine, st, meta)

    await run_turn(session, "hi")
    await session.reset()

    messages = st.messages(meta.id)
    assert len(messages) == 1
    assert messages[0].role == ROLE_SYSTEM


@pytest.mark.asyncio
async def test_conversation_replacement_persists_exact_agent_context(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    initial = [Message(role=ROLE_SYSTEM, content="build")]
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(tmp_path)), initial)
    engine = new_engine_with_executor(StaticReplyExecutor(), initial)
    await engine.ready()
    session = persistent_session(engine, st, meta)

    await session.replace_conversation([Message(role=ROLE_SYSTEM, content="plan")])

    messages = st.messages(meta.id)
    assert len(messages) == 1
    assert messages[0].content == "plan"


@pytest.mark.asyncio
async def test_compact_keeps_system_instructions_and_newest_context(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    initial = [Message(role=ROLE_SYSTEM, content="rules")]
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(tmp_path)), initial)
    messages = [
        *initial,
        Message(role=ROLE_USER, content="one"),
        Message(role=ROLE_ASSISTANT, content="two"),
        Message(role=ROLE_USER, content="three"),
        Message(role=ROLE_ASSISTANT, content="four"),
    ]
    engine = new_engine_with_executor(StaticReplyExecutor(), messages)
    await engine.replace_messages(messages)
    session = persistent_session(engine, st, meta)

    await session.compact(live_context(), "", 2)

    got = session.snapshot().messages
    assert len(got) == 4
    assert got[0].role == ROLE_SYSTEM
    assert got[0].content == "rules"
    assert got[1].role == ROLE_SYSTEM
    assert got[1].content == "Conversation summary:\nmodel summary"
    assert got[2].content == "three"
    assert got[3].content == "four"


@pytest.mark.asyncio
async def test_compact_keeps_assistant_for_retained_tool_results(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    call1 = ToolCall(id="call-1", name="first")
    call2 = ToolCall(id="call-2", name="second")
    messages = [
        Message(role=ROLE_SYSTEM, content="rules"),
        Message(role=ROLE_ASSISTANT, tool_calls=(call1, call2)),
        Message(role=ROLE_TOOL, tool_call_id=call1.id, tool_name=call1.name, content="one"),
        Message(role=ROLE_TOOL, tool_call_id=call2.id, tool_name=call2.name, content="two"),
        Message(role=ROLE_USER, content="next"),
        Message(role=ROLE_ASSISTANT, content="done"),
    ]
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(tmp_path)), messages)
    engine = new_engine_with_executor(StaticReplyExecutor(), messages)
    await engine.replace_messages(messages)
    session = persistent_session(engine, st, meta)

    await session.compact(live_context(), "summary", 3)

    got = session.snapshot().messages
    assert len(got) == 7
    assert got[2].role == ROLE_ASSISTANT
    assert got[2].tool_calls is not None
    assert len(got[2].tool_calls) == 2
    assert got[3].tool_call_id == call1.id
    assert got[4].tool_call_id == call2.id


@pytest.mark.asyncio
async def test_compact_skips_summary_when_history_already_fits(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    messages = [
        Message(role=ROLE_SYSTEM, content="rules"),
        Message(role=ROLE_USER, content="one"),
        Message(role=ROLE_ASSISTANT, content="two"),
    ]
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(tmp_path)), messages)
    executor = StaticReplyExecutor()
    engine = new_engine_with_executor(executor, messages)
    await engine.replace_messages(messages)
    session = persistent_session(engine, st, meta)

    await session.compact(live_context(), "", 4)

    assert executor.calls == 0
    assert len(session.snapshot().messages) == len(messages)


@pytest.mark.asyncio
async def test_compact_does_not_duplicate_transcript_on_resume(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    initial = [Message(role=ROLE_SYSTEM, content="rules")]
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(tmp_path)), initial)
    engine = new_engine_with_executor(StaticReplyExecutor(), initial)
    await engine.ready()
    session = persistent_session(engine, st, meta)

    for query in ("one", "two", "three"):
        await run_turn(session, query)
    await session.compact(live_context(), "summary", 2)
    await run_turn(session, "four")

    messages = st.messages(meta.id)
    rules = sum(1 for m in messages if m.role == ROLE_SYSTEM and m.content == "rules")
    summaries = sum(1 for m in messages if m.content == "Conversation summary:\nsummary")
    user_fours = sum(1 for m in messages if m.role == ROLE_USER and m.content == "four")
    # Kept messages are persisted through the compaction record; re-emitting them
    # as appended messages would duplicate the transcript on replay.
    assert rules == 1
    assert summaries == 1
    assert user_fours == 1


@pytest.mark.asyncio
async def test_undo_restores_write_file_checkpoint(tmp_path: Path) -> None:
    directory = tmp_path / "work"
    directory.mkdir()
    path = directory / "fixture.txt"
    path.write_text("before")
    st = store.new(str(tmp_path / "store"))
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(directory)), [])
    checkpoint = store.Checkpoint(
        id="cp1",
        files=(store.FileSnapshot(path=str(path), exists=True, content="before", mode=0o644),),
    )
    st.append(meta.id, store.Record(type=store.EVENT_CHECKPOINT, checkpoint=checkpoint))
    path.write_text("after")

    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, meta)
    await session.undo()

    assert path.read_text() == "before"


@pytest.mark.asyncio
async def test_undo_truncates_transcript_after_checkpoint(tmp_path: Path) -> None:
    directory = tmp_path / "work"
    directory.mkdir()
    path = directory / "fixture.txt"
    path.write_text("before")
    st = store.new(str(tmp_path / "store"))
    meta = st.create(store.Metadata(provider="test", model="test-model", cwd=str(directory)), [])
    st.append(
        meta.id,
        store.Record(type=store.EVENT_MESSAGE_APPENDED, message=Message(role=ROLE_USER, content="write file")),
    )
    checkpoint = store.Checkpoint(
        id="cp1",
        files=(store.FileSnapshot(path=str(path), exists=True, content="before", mode=0o644),),
    )
    st.append(meta.id, store.Record(type=store.EVENT_CHECKPOINT, checkpoint=checkpoint))
    call = ToolCall(id="call-1", name="write_file")
    st.append(meta.id, store.Record(type=store.EVENT_TOOL_RESULT, tool_call=call, result="wrote"))
    st.append(
        meta.id,
        store.Record(type=store.EVENT_MESSAGE_APPENDED, message=Message(role=ROLE_ASSISTANT, content="done")),
    )
    path.write_text("after")

    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, meta)
    await session.undo()

    assert path.read_text() == "before"
    messages = session.snapshot().messages
    assert len(messages) == 1
    assert messages[0].role == ROLE_USER
    assert messages[0].content == "write file"
    replayed = st.messages(meta.id)
    assert len(replayed) == 1
    assert replayed[0].content == "write file"


def test_session_checkpoint_uses_workspace_and_repository_ports(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    meta = st.create(store.Metadata(provider="test"), [])
    engine = new_engine_with_executor(StaticReplyExecutor(), None)
    port = CheckpointWorkspace([FileSnapshot(path="/work/file", exists=True, content="before", mode=0o644)])
    session = new_persistent_session(engine, store.new_repository(st), port, Metadata(id=SessionID(meta.id)))

    session.checkpoint(ToolCall(name="write_file", input='{"path":"file"}'))

    assert port.paths == ["file"]
    records = st.records(meta.id)
    last = records[-1]
    assert last.type == store.EVENT_CHECKPOINT
    assert last.checkpoint is not None
    assert last.checkpoint.files[0].content == "before"


@pytest.mark.asyncio
async def test_session_run_turn_refuses_a_second_turn(tmp_path: Path) -> None:
    release = asyncio.Event()
    model = BlockingModel(release)
    engine = new_engine(model, FakeToolRunner(), None)
    await engine.ready()
    session = new_session(engine)
    notifications, approvals = new_queues()
    first = asyncio.create_task(session.run_turn(live_context(), "one", notifications, approvals))
    await model.started.wait()

    second_notifications, second_approvals = new_queues()
    with pytest.raises(RuntimeError, match="session is already running a turn"):
        await session.run_turn(live_context(), "two", second_notifications, second_approvals)
    # The busy path still closes the notification queue, so a consumer that is
    # draining it terminates instead of waiting forever.
    assert isinstance(second_notifications.get_nowait(), NotificationsClosed)

    release.set()
    await first


def test_store_serializes_concurrent_appends(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    meta = st.create(store.Metadata(provider="test"), [])
    st.set_current_turn(meta.id, store.TurnID("turn-1"))

    def append() -> None:
        st.append(
            meta.id,
            store.Record(type=store.EVENT_MESSAGE_APPENDED, message=Message(role=ROLE_USER, content="x")),
        )

    threads = [threading.Thread(target=append) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = st.records(meta.id)
    appended = [record for record in records if record.type == store.EVENT_MESSAGE_APPENDED]
    assert len(appended) == 20
    for record in appended:
        assert record.turn_id == "turn-1", "a torn meta.json interleave lost the turn id"


# --- store-only cases --------------------------------------------------------


def test_store_rejects_session_ids_that_escape_root(tmp_path: Path) -> None:
    st = store.new(str(tmp_path / "store"))
    for bad in ("../..", "..", ".", "a/b", "_memory", "", "x y"):
        session_id = store.SessionID(bad)
        with pytest.raises(ValueError):
            st.delete(session_id)
        with pytest.raises(ValueError):
            st.append(session_id, store.Record(type=store.EVENT_SESSION_STARTED))


def test_store_heals_torn_tail_line(tmp_path: Path) -> None:
    root = tmp_path / "store"
    st = store.new(str(root))
    meta = st.create(store.Metadata(), [])
    st.append(
        meta.id,
        store.Record(type=store.EVENT_MESSAGE_APPENDED, message=Message(role=ROLE_USER, content="before crash")),
    )
    # Simulate a crash mid-append by appending a truncated JSON line.
    events_path = root / str(meta.id) / "events.jsonl"
    with events_path.open("ab") as handle:
        handle.write(b'{"type":"message_app')

    messages = st.messages(meta.id)
    assert len(messages) == 1
    assert messages[0].content == "before crash"

    # The healed file must accept new appends.
    st.append(
        meta.id,
        store.Record(type=store.EVENT_MESSAGE_APPENDED, message=Message(role=ROLE_USER, content="after crash")),
    )
    messages = st.messages(meta.id)
    assert len(messages) == 2
    assert messages[1].content == "after crash"


def test_store_fails_loudly_on_mid_file_corruption_with_byte_offset(tmp_path: Path) -> None:
    root = tmp_path / "store"
    st = store.new(str(root))
    meta = st.create(store.Metadata(), [])
    for content in ("one", "two", "three"):
        st.append(
            meta.id,
            store.Record(type=store.EVENT_MESSAGE_APPENDED, message=Message(role=ROLE_USER, content=content)),
        )
    events_path = root / str(meta.id) / "events.jsonl"
    lines = events_path.read_bytes().split(b"\n")
    prefix = b"\n".join(lines[:2]) + b"\n"
    offset = len(prefix)
    events_path.write_bytes(prefix + b"NOT JSON\n" + b"\n".join(lines[2:]))

    # The failure names the byte offset of the record that could not be read,
    # rather than silently dropping it and rewriting history.
    with pytest.raises(ValueError, match=f"corrupt session event at byte {offset}"):
        st.messages(meta.id)


def test_store_rejects_an_event_line_over_the_read_cap(tmp_path: Path) -> None:
    assert store.MAX_EVENT_BYTES == 20 * 1024 * 1024
    root = tmp_path / "store"
    st = store.new(str(root))
    meta = st.create(store.Metadata(), [])
    events_path = root / str(meta.id) / "events.jsonl"
    oversized = b"x" * (20 * 1024 * 1024)
    with events_path.open("ab") as handle:
        handle.write(
            b'{"type":"message_appended","session_id":"'
            + str(meta.id).encode()
            + b'","time":"2026-09-16T12:00:00Z","message":{"role":"user","content":"'
            + oversized
            + b'"}}\n'
        )

    with pytest.raises(ValueError, match="read cap"):
        st.messages(meta.id)


def test_new_id_and_new_turn_id_carry_nine_fractional_digits() -> None:
    fixed = dt.datetime(2026, 9, 16, 12, 0, 0, 123456, tzinfo=dt.UTC)
    assert store.new_id(fixed) == "20260916T120000123456000"
    assert store.new_turn_id(fixed) == "20260916T120000.123456000"

    # The whole point of the hand-rolled fraction: strftime("%f") stops at six
    # digits, so the live identifiers are checked against a nine-digit pattern.
    assert re.fullmatch(r"\d{8}T\d{6}\d{9}", store.new_id(dt.datetime.now(dt.UTC)))
    assert re.fullmatch(r"\d{8}T\d{6}\.\d{9}", store.new_turn_id(dt.datetime.now(dt.UTC)))


def test_validate_id_accepts_its_own_basename_and_rejects_everything_else() -> None:
    for good in ("20260916T120000123456789", "a-b_C1", "x"):
        store.validateID(store.SessionID(good))

    for bad in ("../..", "a/b", "_memory", "", ".", "..", "x y", "a.b", "a\u00e9"):
        with pytest.raises(ValueError):
            store.validateID(store.SessionID(bad))


def test_metadata_writes_are_atomic_with_expected_modes_and_layout(tmp_path: Path) -> None:
    root = tmp_path / "store"
    st = store.new(str(root))
    meta = st.create(store.Metadata(title="atomic"), [])

    session_dir = root / str(meta.id)
    meta_path = session_dir / "meta.json"
    raw = meta_path.read_bytes()
    assert raw.endswith(b"\n")
    assert b'\n  "id":' in raw
    assert stat.S_IMODE(meta_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(session_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    # No temp file survives the atomic replace.
    assert sorted(path.name for path in session_dir.iterdir()) == ["events.jsonl", "meta.json"]

    st.save_memory(["remember this"])
    memory_path = root / "_memory.json"
    assert memory_path.read_bytes() == b'[\n  "remember this"\n]\n'
    assert stat.S_IMODE(memory_path.stat().st_mode) == 0o600


# --- golden store replay -----------------------------------------------------


def test_store_replays_a_checked_in_session_store(tmp_path: Path) -> None:
    """Read the checked-in golden store and replay every message path."""
    sessions = tmp_path / "sessions"
    shutil.copytree(SESSION_STORE_FIXTURE, sessions)
    st = store.new(str(sessions))
    session_id = store.SessionID("20260916T120000123456789")
    session_key = SessionID(str(session_id))

    records = st.records(session_id)
    assert [record.type for record in records] == [
        store.EVENT_SESSION_STARTED,
        store.EVENT_MESSAGE_APPENDED,
        store.EVENT_SESSION_STARTED,
        store.EVENT_MESSAGE_APPENDED,
        store.EVENT_MESSAGE_APPENDED,
        store.EVENT_MESSAGE_APPENDED,
        store.EVENT_TOOL_RESULT,
        store.EVENT_APPROVAL_DECISION,
        store.EVENT_CHECKPOINT,
        store.EVENT_ERROR,
        store.EVENT_CANCEL,
        store.EVENT_COMPACT,
        store.EVENT_RESET,
        store.EVENT_CONTEXT_REPLACED,
    ]

    # 1. appended messages, including a tool-call message and reasoning content.
    appended = [record for record in records if record.type == store.EVENT_MESSAGE_APPENDED]
    assert store.messagesFromRecords(appended) == [
        Message(role=ROLE_SYSTEM, content="system prompt"),
        Message(role=ROLE_USER, content="hello"),
        Message(role=ROLE_ASSISTANT, content="hi there", reasoning_content="thinking"),
        Message(
            role=ROLE_ASSISTANT,
            content="calling",
            tool_calls=(ToolCall(id="call-1", name="bash", input='{"command":"pwd"}'),),
        ),
    ]
    # 2. tool results become ordinary tool messages.
    tool_results = [record for record in records if record.type == store.EVENT_TOOL_RESULT]
    assert store.messagesFromRecords(tool_results) == [
        Message(role=ROLE_TOOL, tool_call_id="call-1", tool_name="bash", content="/tmp/workspace")
    ]
    # 3. compaction replaces the transcript with its own kept_messages.
    compact = next(record for record in records if record.type == store.EVENT_COMPACT)
    assert compact.compact is not None
    assert store.messagesFromRecords([compact]) == list(compact.compact.kept_messages)
    # 4. reset keeps only the system messages that survived compaction.
    assert store.messagesFromRecords(records[:13]) == list(compact.compact.kept_messages)
    # 5. context replacement is authoritative; the final replay is exactly it.
    assert st.messages(session_id) == [Message(role=ROLE_SYSTEM, content="replaced")]

    summaries = st.list()
    assert [summary.id for summary in summaries] == [
        store.SessionID("20260916T130000000000000"),
        session_id,
    ]
    assert summaries[1].title == "fixture session"
    assert summaries[1].provider == "deepseek"
    assert summaries[1].model == "deepseek-reasoner"

    repository = store.new_repository(st)
    messages, meta = repository.load(session_key)
    assert messages == [Message(role=ROLE_SYSTEM, content="replaced")]
    assert session_key == meta.id
    assert meta.cwd == "/tmp/workspace"
    assert meta.project_id == "/tmp/workspace"
    assert meta.config_root == "/home/user"
    assert meta.instruction_sources == ("AGENTS.md", "/home/user/.superagent/AGENTS.md")
    assert meta.workspace_spec is not None
    assert meta.workspace_spec.primary_root == "/tmp/workspace"
    assert meta.workspace_spec.roots[0].access == "read_write"

    audit = repository.load_audit_events(session_key)
    assert [event.type for event in audit] == [
        store.EVENT_TOOL_RESULT,
        store.EVENT_APPROVAL_DECISION,
        store.EVENT_ERROR,
        store.EVENT_CANCEL,
    ]
    assert audit[0].tool_call is not None
    assert audit[0].tool_call.name == "bash"
    assert audit[0].result == "/tmp/workspace"
    assert audit[1].decision == "once"
    assert audit[2].error == "something failed"

    assert st.load_memory() == ["remember this", "and this"]

    files, undo_messages, index = repository.load_undo_point(session_key)
    assert index == 8
    assert undo_messages == store.messagesFromRecords(records[:8])
    assert len(undo_messages) == 5
    assert len(files) == 1
    assert files[0].path == "a.txt"
    assert files[0].exists is True
    assert files[0].content == "old"
    assert files[0].mode == 420

    # The fixture is a copy, so mutating it proves the store writes what it read.
    st.append(
        session_id,
        store.Record(type=store.EVENT_MESSAGE_APPENDED, message=Message(role=ROLE_USER, content="after replay")),
    )
    assert st.messages(session_id)[-1].content == "after replay"
