"""The store, and the session use cases that run over it.

Ported from ``tests/runtime/session_store_test.go``. Two of the Go file's cases
have no counterpart here because their subject is engine behaviour, not storage,
and they already live in ``test_engine.py``:

* ``TestConcurrentRunTurnFailsWithoutBlockingEvents`` — the engine half is
  ``test_invalid_second_turn_does_not_cancel_active_run``; the session half, the
  busy error, is ``test_session_run_turn_refuses_a_second_turn`` below.
* ``TestResolverErrorLeavesNoToolMessageWhenNothingWasAsked`` —
  ``test_no_tools_tool_call_is_protocol_error``.

The store-only cases the milestone calls for beyond the Go file are at the end:
cross-language replay of a real Go store, the torn tail and mid-file corruption
pairs, the 20 MiB read cap, identifiers, and atomic metadata writes.
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
from super_agent.runtime.engine import Engine, NewEngine, NewEngineWithExecutor
from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import (
    Message,
    RoleAssistant,
    RoleSystem,
    RoleTool,
    RoleUser,
    ToolCall,
)
from super_agent.runtime.session import (
    ApprovalsClosed,
    FileSnapshot,
    Metadata,
    NewPersistentSession,
    NewSession,
    NotificationsClosed,
    Session,
    SessionID,
    SessionNotification,
    WorkspaceAccessReadWrite,
    WorkspaceRootSpec,
    WorkspaceSpec,
)
from tests.fakes.execution import FakeToolRunner, StaticReplyExecutor
from tests.fakes.model import BlockingModel

#: A real store written by the Go implementation. Read-only here; every test that
#: mutates it copies it to ``tmp_path`` first.
GO_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "go_sessions"

#: Go writes ``store.Metadata{ID: "new"}`` for a session whose stored id does not
#: matter yet; the explicit conversion stands in for the untyped constant.
NEW_ID = store.SessionID("new")


# --- helpers -----------------------------------------------------------------


def configured_workspace(root: str) -> workspace.Workspace:
    """Mirror the Go tests' ``configuredWorkspace`` helper."""
    return workspace.New(workspace.NewDefaultContext(root))


def persistent_session(engine: Engine, st: store.Store, meta: store.Metadata) -> Session:
    """Mirror the Go tests' ``persistentSession`` helper."""
    root = meta.CWD or os.getcwd()
    return NewPersistentSession(
        engine,
        store.NewRepository(st),
        configured_workspace(root),
        Metadata(
            ID=SessionID(meta.ID),
            Title=meta.Title,
            Provider=meta.Provider,
            Model=meta.Model,
            CWD=meta.CWD,
            InstructionSources=tuple(meta.InstructionSources),
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
    await session.RunTurn(LiveContext(), query, notifications, approvals)


class CheckpointWorkspace:
    """Mirrors Go's ``checkpointWorkspace``: a port stub that records what it captured."""

    def __init__(self, files: list[FileSnapshot]) -> None:
        self.paths: list[str] = []
        self.files: list[FileSnapshot] = files

    def Spec(self) -> WorkspaceSpec:
        return WorkspaceSpec(
            PrimaryRoot="/work",
            CWD="/work",
            Roots=(WorkspaceRootSpec(Path="/work", Access=WorkspaceAccessReadWrite),),
        )

    def Validate(self, spec: WorkspaceSpec) -> None:
        return None

    def Canonicalize(self, spec: WorkspaceSpec) -> WorkspaceSpec:
        return spec

    def Activate(self, spec: WorkspaceSpec) -> None:
        return None

    def Capture(self, paths: list[str]) -> list[FileSnapshot]:
        self.paths = list(paths)
        return self.files

    def Restore(self, files: list[FileSnapshot]) -> None:
        return None


class FailingWorkspaceRepository(store.Repository):
    """Mirrors Go's ``failingWorkspaceRepository``: the migration write always fails."""

    def SaveWorkspaceDescription(self, session_id: SessionID, spec: WorkspaceSpec) -> None:
        raise RuntimeError("workspace metadata write failed")


class CanonicalizeSpy(workspace.Workspace):
    """Mirrors Go's ``canonicalizeSpy``: counts how often the legacy upgrade ran."""

    def __init__(self, context: workspace.Context) -> None:
        super().__init__(context)
        self.calls = 0

    def Canonicalize(self, spec: WorkspaceSpec) -> WorkspaceSpec:
        self.calls += 1
        return super().Canonicalize(spec)


# --- session over the store --------------------------------------------------


@pytest.mark.asyncio
async def test_persistent_session_resumes_conversation_with_tool_results(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    initial = [Message(Role=RoleSystem, Content="rules")]
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(cwd)), initial)
    st.Append(meta.ID, store.Record(Type=store.EventMessageAppended, Message=Message(Role=RoleUser, Content="hi")))
    call = ToolCall(ID="call-1", Name="read_file")
    st.Append(meta.ID, store.Record(Type=store.EventToolResult, ToolCall=call, Result="file contents"))

    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(ID=NEW_ID))
    await session.Resume(SessionID(meta.ID))

    messages = session.Snapshot().Messages
    assert len(messages) == 3
    assert messages[2].Role == RoleTool
    assert messages[2].Content == "file contents"
    assert messages[2].ToolCallID == "call-1"


@pytest.mark.asyncio
async def test_resume_migrates_legacy_workspace_to_canonical_spec(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    legacy_cwd = str(tmp_path / "legacy")
    os.mkdir(legacy_cwd)
    meta = st.Create(
        store.Metadata(
            Title="legacy",
            Provider="test",
            Model="test-model",
            CWD=legacy_cwd,
            InstructionSources=("AGENTS.md",),
        ),
        [Message(Role=RoleSystem, Content="rules")],
    )
    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(ID=NEW_ID))
    await session.Resume(SessionID(meta.ID))

    canonical = os.path.realpath(legacy_cwd)
    persisted = st.Metadata(meta.ID)
    assert persisted.Workspace is not None
    assert persisted.Workspace.PrimaryRoot == canonical
    assert canonical == persisted.Workspace.CWD
    assert canonical == persisted.CWD
    assert len(persisted.Workspace.Roots) == 1
    assert persisted.Workspace.Roots[0].Path == canonical
    assert persisted.Workspace.Roots[0].Access == "read_write"
    # Migration touches only the workspace description: unrelated metadata,
    # including ProjectID and ConfigRoot, keeps its saved value.
    assert persisted.Title == "legacy"
    assert persisted.Provider == "test"
    assert persisted.Model == "test-model"
    assert persisted.CreatedAt == meta.CreatedAt
    assert persisted.InstructionSources == ("AGENTS.md",)
    assert persisted.ProjectID == ""
    assert persisted.ConfigRoot == ""


@pytest.mark.asyncio
async def test_resume_legacy_workspace_migration_is_idempotent(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    legacy_cwd = str(tmp_path / "legacy")
    os.mkdir(legacy_cwd)
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=legacy_cwd), [])
    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(ID=NEW_ID))
    await session.Resume(SessionID(meta.ID))
    first = st.Metadata(meta.ID)
    assert first.Workspace is not None

    await session.Resume(SessionID(meta.ID))
    second = st.Metadata(meta.ID)
    assert first.Workspace == second.Workspace


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform == "win32", reason="symlinks commonly need elevated privileges on Windows")
async def test_resume_uses_strict_validation_after_legacy_migration(tmp_path: Path) -> None:
    saved_root = tmp_path / "project"
    saved_root.mkdir()
    st = store.New(str(tmp_path / "store"))
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(saved_root)), [])
    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(ID=NEW_ID))
    await session.Resume(SessionID(meta.ID))

    # The one-time upgrade persisted the canonical root, so replacing it with an
    # escaping symlink must now fail the strict check instead of silently
    # adopting the new target.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.rmdir(saved_root)
    os.symlink(elsewhere, saved_root)
    with pytest.raises(ValueError, match="saved workspace is no longer valid"):
        await session.Resume(SessionID(meta.ID))


@pytest.mark.asyncio
async def test_resume_does_not_fall_back_to_legacy_when_saved_spec_is_invalid(tmp_path: Path) -> None:
    valid_cwd = str(tmp_path / "valid")
    os.mkdir(valid_cwd)
    missing = str(tmp_path / "missing")
    st = store.New(str(tmp_path / "store"))
    spec = WorkspaceSpec(
        PrimaryRoot=missing,
        CWD=missing,
        Roots=(WorkspaceRootSpec(Path=missing, Access=WorkspaceAccessReadWrite),),
    )
    meta = store.NewRepository(st).Create(
        Metadata(Provider="test", Model="test-model", CWD=valid_cwd, WorkspaceSpec=spec),
        [],
    )
    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, store.Metadata(ID=NEW_ID))
    with pytest.raises(ValueError, match="saved workspace is no longer valid"):
        await session.Resume(SessionID(meta.ID))

    persisted = st.Metadata(store.SessionID(meta.ID))
    assert persisted.Workspace is not None
    assert persisted.Workspace.PrimaryRoot == missing


@pytest.mark.asyncio
async def test_resume_fails_when_workspace_migration_cannot_be_persisted(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    legacy_cwd = str(tmp_path / "legacy")
    os.mkdir(legacy_cwd)
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=legacy_cwd), [])
    repository = FailingWorkspaceRepository(st)
    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    session = NewPersistentSession(
        engine, repository, configured_workspace(str(tmp_path)), Metadata(ID=SessionID("new"))
    )
    with pytest.raises(ValueError, match="persist migrated workspace"):
        await session.Resume(SessionID(meta.ID))

    persisted = st.Metadata(meta.ID)
    assert persisted.Workspace is None


@pytest.mark.asyncio
async def test_resume_upgrades_legacy_workspace_only_once(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    legacy_cwd = str(tmp_path / "legacy")
    os.mkdir(legacy_cwd)
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=legacy_cwd), [])
    spy = CanonicalizeSpy(workspace.NewDefaultContext(str(tmp_path)))
    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    session = NewPersistentSession(engine, store.NewRepository(st), spy, Metadata(ID=SessionID("new")))

    await session.Resume(SessionID(meta.ID))
    assert spy.calls == 1
    await session.Resume(SessionID(meta.ID))
    assert spy.calls == 1


def test_session_list_preserves_parent_relationship(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    repository = store.NewRepository(st)
    created = repository.Create(Metadata(ParentID=SessionID("parent"), Provider="test", Model="model"), [])

    items = repository.List()

    assert len(items) == 1
    assert items[0].ID == created.ID
    assert items[0].ParentID == "parent"


@pytest.mark.asyncio
async def test_session_fork_copies_transcript_and_selects_child(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    initial = [Message(Role=RoleSystem, Content="rules")]
    meta = st.Create(store.Metadata(Title="parent", Provider="test", Model="model", CWD=str(tmp_path)), initial)
    engine = NewEngineWithExecutor(StaticReplyExecutor(), initial)
    await engine.Ready()
    session = persistent_session(engine, st, meta)

    child = await session.Fork("experiment")

    assert child.ParentID == SessionID(meta.ID)
    assert session.Metadata().ID == child.ID
    messages, _meta = store.NewRepository(st).Load(child.ID)
    assert len(messages) == 1
    assert messages[0].Content == "rules"


@pytest.mark.asyncio
async def test_cross_session_memory_updates_transcript(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    initial = [Message(Role=RoleSystem, Content="rules"), Message(Role=RoleUser, Content="hello")]
    meta = st.Create(store.Metadata(Title="memory", CWD=str(tmp_path)), initial)
    engine = NewEngineWithExecutor(StaticReplyExecutor(), initial)
    await engine.Ready()
    session = persistent_session(engine, st, meta)

    await session.Remember("Prefer concise answers")
    assert session.Memories() == ["Prefer concise answers"]

    messages = session.Snapshot().Messages
    assert len(messages) == 3
    assert "Prefer concise answers" in messages[0].Content
    assert messages[2].Content == "hello"

    await session.ForgetMemories()
    assert len(session.Snapshot().Messages) == 2


@pytest.mark.asyncio
async def test_persistent_reset_preserves_system_messages(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    initial = [Message(Role=RoleSystem, Content="rules")]
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(tmp_path)), initial)
    engine = NewEngineWithExecutor(StaticReplyExecutor(), initial)
    await engine.Ready()
    session = persistent_session(engine, st, meta)

    await run_turn(session, "hi")
    await session.Reset()

    messages = st.Messages(meta.ID)
    assert len(messages) == 1
    assert messages[0].Role == RoleSystem


@pytest.mark.asyncio
async def test_conversation_replacement_persists_exact_agent_context(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    initial = [Message(Role=RoleSystem, Content="build")]
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(tmp_path)), initial)
    engine = NewEngineWithExecutor(StaticReplyExecutor(), initial)
    await engine.Ready()
    session = persistent_session(engine, st, meta)

    await session.ReplaceConversation([Message(Role=RoleSystem, Content="plan")])

    messages = st.Messages(meta.ID)
    assert len(messages) == 1
    assert messages[0].Content == "plan"


@pytest.mark.asyncio
async def test_compact_keeps_system_instructions_and_newest_context(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    initial = [Message(Role=RoleSystem, Content="rules")]
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(tmp_path)), initial)
    messages = [
        *initial,
        Message(Role=RoleUser, Content="one"),
        Message(Role=RoleAssistant, Content="two"),
        Message(Role=RoleUser, Content="three"),
        Message(Role=RoleAssistant, Content="four"),
    ]
    engine = NewEngineWithExecutor(StaticReplyExecutor(), messages)
    await engine.ReplaceMessages(messages)
    session = persistent_session(engine, st, meta)

    await session.Compact(LiveContext(), "", 2)

    got = session.Snapshot().Messages
    assert len(got) == 4
    assert got[0].Role == RoleSystem
    assert got[0].Content == "rules"
    assert got[1].Role == RoleSystem
    assert got[1].Content == "Conversation summary:\nmodel summary"
    assert got[2].Content == "three"
    assert got[3].Content == "four"


@pytest.mark.asyncio
async def test_compact_keeps_assistant_for_retained_tool_results(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    call1 = ToolCall(ID="call-1", Name="first")
    call2 = ToolCall(ID="call-2", Name="second")
    messages = [
        Message(Role=RoleSystem, Content="rules"),
        Message(Role=RoleAssistant, ToolCalls=(call1, call2)),
        Message(Role=RoleTool, ToolCallID=call1.ID, ToolName=call1.Name, Content="one"),
        Message(Role=RoleTool, ToolCallID=call2.ID, ToolName=call2.Name, Content="two"),
        Message(Role=RoleUser, Content="next"),
        Message(Role=RoleAssistant, Content="done"),
    ]
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(tmp_path)), messages)
    engine = NewEngineWithExecutor(StaticReplyExecutor(), messages)
    await engine.ReplaceMessages(messages)
    session = persistent_session(engine, st, meta)

    await session.Compact(LiveContext(), "summary", 3)

    got = session.Snapshot().Messages
    assert len(got) == 7
    assert got[2].Role == RoleAssistant
    assert got[2].ToolCalls is not None
    assert len(got[2].ToolCalls) == 2
    assert got[3].ToolCallID == call1.ID
    assert got[4].ToolCallID == call2.ID


@pytest.mark.asyncio
async def test_compact_skips_summary_when_history_already_fits(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    messages = [
        Message(Role=RoleSystem, Content="rules"),
        Message(Role=RoleUser, Content="one"),
        Message(Role=RoleAssistant, Content="two"),
    ]
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(tmp_path)), messages)
    executor = StaticReplyExecutor()
    engine = NewEngineWithExecutor(executor, messages)
    await engine.ReplaceMessages(messages)
    session = persistent_session(engine, st, meta)

    await session.Compact(LiveContext(), "", 4)

    assert executor.calls == 0
    assert len(session.Snapshot().Messages) == len(messages)


@pytest.mark.asyncio
async def test_compact_does_not_duplicate_transcript_on_resume(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    initial = [Message(Role=RoleSystem, Content="rules")]
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(tmp_path)), initial)
    engine = NewEngineWithExecutor(StaticReplyExecutor(), initial)
    await engine.Ready()
    session = persistent_session(engine, st, meta)

    for query in ("one", "two", "three"):
        await run_turn(session, query)
    await session.Compact(LiveContext(), "summary", 2)
    await run_turn(session, "four")

    messages = st.Messages(meta.ID)
    rules = sum(1 for m in messages if m.Role == RoleSystem and m.Content == "rules")
    summaries = sum(1 for m in messages if m.Content == "Conversation summary:\nsummary")
    user_fours = sum(1 for m in messages if m.Role == RoleUser and m.Content == "four")
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
    st = store.New(str(tmp_path / "store"))
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(directory)), [])
    checkpoint = store.Checkpoint(
        ID="cp1",
        Files=(store.FileSnapshot(Path=str(path), Exists=True, Content="before", Mode=0o644),),
    )
    st.Append(meta.ID, store.Record(Type=store.EventCheckpoint, Checkpoint=checkpoint))
    path.write_text("after")

    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, meta)
    await session.Undo()

    assert path.read_text() == "before"


@pytest.mark.asyncio
async def test_undo_truncates_transcript_after_checkpoint(tmp_path: Path) -> None:
    directory = tmp_path / "work"
    directory.mkdir()
    path = directory / "fixture.txt"
    path.write_text("before")
    st = store.New(str(tmp_path / "store"))
    meta = st.Create(store.Metadata(Provider="test", Model="test-model", CWD=str(directory)), [])
    st.Append(
        meta.ID,
        store.Record(Type=store.EventMessageAppended, Message=Message(Role=RoleUser, Content="write file")),
    )
    checkpoint = store.Checkpoint(
        ID="cp1",
        Files=(store.FileSnapshot(Path=str(path), Exists=True, Content="before", Mode=0o644),),
    )
    st.Append(meta.ID, store.Record(Type=store.EventCheckpoint, Checkpoint=checkpoint))
    call = ToolCall(ID="call-1", Name="write_file")
    st.Append(meta.ID, store.Record(Type=store.EventToolResult, ToolCall=call, Result="wrote"))
    st.Append(
        meta.ID,
        store.Record(Type=store.EventMessageAppended, Message=Message(Role=RoleAssistant, Content="done")),
    )
    path.write_text("after")

    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    session = persistent_session(engine, st, meta)
    await session.Undo()

    assert path.read_text() == "before"
    messages = session.Snapshot().Messages
    assert len(messages) == 1
    assert messages[0].Role == RoleUser
    assert messages[0].Content == "write file"
    replayed = st.Messages(meta.ID)
    assert len(replayed) == 1
    assert replayed[0].Content == "write file"


def test_session_checkpoint_uses_workspace_and_repository_ports(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    meta = st.Create(store.Metadata(Provider="test"), [])
    engine = NewEngineWithExecutor(StaticReplyExecutor(), None)
    port = CheckpointWorkspace([FileSnapshot(Path="/work/file", Exists=True, Content="before", Mode=0o644)])
    session = NewPersistentSession(engine, store.NewRepository(st), port, Metadata(ID=SessionID(meta.ID)))

    session.Checkpoint(ToolCall(Name="write_file", Input='{"path":"file"}'))

    assert port.paths == ["file"]
    records = st.Records(meta.ID)
    last = records[-1]
    assert last.Type == store.EventCheckpoint
    assert last.Checkpoint is not None
    assert last.Checkpoint.Files[0].Content == "before"


@pytest.mark.asyncio
async def test_session_run_turn_refuses_a_second_turn(tmp_path: Path) -> None:
    release = asyncio.Event()
    model = BlockingModel(release)
    engine = NewEngine(model, FakeToolRunner(), None)
    await engine.Ready()
    session = NewSession(engine)
    notifications, approvals = new_queues()
    first = asyncio.create_task(session.RunTurn(LiveContext(), "one", notifications, approvals))
    await model.started.wait()

    second_notifications, second_approvals = new_queues()
    with pytest.raises(RuntimeError, match="session is already running a turn"):
        await session.RunTurn(LiveContext(), "two", second_notifications, second_approvals)
    # The busy path still closes the notification queue, so a consumer that is
    # draining it terminates instead of waiting forever.
    assert isinstance(second_notifications.get_nowait(), NotificationsClosed)

    release.set()
    await first


def test_store_serializes_concurrent_appends(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    meta = st.Create(store.Metadata(Provider="test"), [])
    st.SetCurrentTurn(meta.ID, store.TurnID("turn-1"))

    def append() -> None:
        st.Append(
            meta.ID,
            store.Record(Type=store.EventMessageAppended, Message=Message(Role=RoleUser, Content="x")),
        )

    threads = [threading.Thread(target=append) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    records = st.Records(meta.ID)
    appended = [record for record in records if record.Type == store.EventMessageAppended]
    assert len(appended) == 20
    for record in appended:
        assert record.TurnID == "turn-1", "a torn meta.json interleave lost the turn id"


# --- store-only cases --------------------------------------------------------


def test_store_rejects_session_ids_that_escape_root(tmp_path: Path) -> None:
    st = store.New(str(tmp_path / "store"))
    for bad in ("../..", "..", ".", "a/b", "_memory", "", "x y"):
        session_id = store.SessionID(bad)
        with pytest.raises(ValueError):
            st.Delete(session_id)
        with pytest.raises(ValueError):
            st.Append(session_id, store.Record(Type=store.EventSessionStarted))


def test_store_heals_torn_tail_line(tmp_path: Path) -> None:
    root = tmp_path / "store"
    st = store.New(str(root))
    meta = st.Create(store.Metadata(), [])
    st.Append(
        meta.ID,
        store.Record(Type=store.EventMessageAppended, Message=Message(Role=RoleUser, Content="before crash")),
    )
    # Simulate a crash mid-append by appending a truncated JSON line.
    events_path = root / str(meta.ID) / "events.jsonl"
    with events_path.open("ab") as handle:
        handle.write(b'{"type":"message_app')

    messages = st.Messages(meta.ID)
    assert len(messages) == 1
    assert messages[0].Content == "before crash"

    # The healed file must accept new appends.
    st.Append(
        meta.ID,
        store.Record(Type=store.EventMessageAppended, Message=Message(Role=RoleUser, Content="after crash")),
    )
    messages = st.Messages(meta.ID)
    assert len(messages) == 2
    assert messages[1].Content == "after crash"


def test_store_fails_loudly_on_mid_file_corruption_with_byte_offset(tmp_path: Path) -> None:
    root = tmp_path / "store"
    st = store.New(str(root))
    meta = st.Create(store.Metadata(), [])
    for content in ("one", "two", "three"):
        st.Append(
            meta.ID,
            store.Record(Type=store.EventMessageAppended, Message=Message(Role=RoleUser, Content=content)),
        )
    events_path = root / str(meta.ID) / "events.jsonl"
    lines = events_path.read_bytes().split(b"\n")
    prefix = b"\n".join(lines[:2]) + b"\n"
    offset = len(prefix)
    events_path.write_bytes(prefix + b"NOT JSON\n" + b"\n".join(lines[2:]))

    # The failure names the byte offset of the record that could not be read,
    # rather than silently dropping it and rewriting history.
    with pytest.raises(ValueError, match=f"corrupt session event at byte {offset}"):
        st.Messages(meta.ID)


def test_store_rejects_an_event_line_over_the_read_cap(tmp_path: Path) -> None:
    assert store.MAX_EVENT_BYTES == 20 * 1024 * 1024
    root = tmp_path / "store"
    st = store.New(str(root))
    meta = st.Create(store.Metadata(), [])
    events_path = root / str(meta.ID) / "events.jsonl"
    oversized = b"x" * (20 * 1024 * 1024)
    with events_path.open("ab") as handle:
        handle.write(
            b'{"type":"message_appended","session_id":"'
            + str(meta.ID).encode()
            + b'","time":"2026-09-16T12:00:00Z","message":{"role":"user","content":"'
            + oversized
            + b'"}}\n'
        )

    with pytest.raises(ValueError, match="read cap"):
        st.Messages(meta.ID)


def test_new_id_and_new_turn_id_carry_nine_fractional_digits() -> None:
    fixed = dt.datetime(2026, 9, 16, 12, 0, 0, 123456, tzinfo=dt.UTC)
    assert store.NewID(fixed) == "20260916T120000123456000"
    assert store.NewTurnID(fixed) == "20260916T120000.123456000"

    # The whole point of the hand-rolled fraction: strftime("%f") stops at six
    # digits, so the live identifiers are checked against a nine-digit pattern.
    assert re.fullmatch(r"\d{8}T\d{6}\d{9}", store.NewID(dt.datetime.now(dt.UTC)))
    assert re.fullmatch(r"\d{8}T\d{6}\.\d{9}", store.NewTurnID(dt.datetime.now(dt.UTC)))


def test_validate_id_accepts_its_own_basename_and_rejects_everything_else() -> None:
    for good in ("20260916T120000123456789", "a-b_C1", "x"):
        store.validateID(store.SessionID(good))

    for bad in ("../..", "a/b", "_memory", "", ".", "..", "x y", "a.b", "a\u00e9"):
        with pytest.raises(ValueError):
            store.validateID(store.SessionID(bad))


def test_metadata_writes_are_atomic_with_expected_modes_and_layout(tmp_path: Path) -> None:
    root = tmp_path / "store"
    st = store.New(str(root))
    meta = st.Create(store.Metadata(Title="atomic"), [])

    session_dir = root / str(meta.ID)
    meta_path = session_dir / "meta.json"
    raw = meta_path.read_bytes()
    assert raw.endswith(b"\n")
    assert b'\n  "id":' in raw
    assert stat.S_IMODE(meta_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(session_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    # No temp file survives the atomic replace.
    assert sorted(path.name for path in session_dir.iterdir()) == ["events.jsonl", "meta.json"]

    st.SaveMemory(["remember this"])
    memory_path = root / "_memory.json"
    assert memory_path.read_bytes() == b'[\n  "remember this"\n]\n'
    assert stat.S_IMODE(memory_path.stat().st_mode) == 0o600


# --- cross-language replay ---------------------------------------------------


def test_store_replays_a_real_go_session(tmp_path: Path) -> None:
    """Read a fixture written by the Go implementation and replay every message path."""
    sessions = tmp_path / "sessions"
    shutil.copytree(GO_FIXTURE, sessions)
    st = store.New(str(sessions))
    session_id = store.SessionID("20260916T120000123456789")
    session_key = SessionID(str(session_id))

    records = st.Records(session_id)
    assert [record.Type for record in records] == [
        store.EventSessionStarted,
        store.EventMessageAppended,
        store.EventSessionStarted,
        store.EventMessageAppended,
        store.EventMessageAppended,
        store.EventMessageAppended,
        store.EventToolResult,
        store.EventApprovalDecision,
        store.EventCheckpoint,
        store.EventError,
        store.EventCancel,
        store.EventCompact,
        store.EventReset,
        store.EventContextReplaced,
    ]

    # 1. appended messages, including a tool-call message and reasoning content.
    appended = [record for record in records if record.Type == store.EventMessageAppended]
    assert store.messagesFromRecords(appended) == [
        Message(Role=RoleSystem, Content="system prompt"),
        Message(Role=RoleUser, Content="hello"),
        Message(Role=RoleAssistant, Content="hi there", ReasoningContent="thinking"),
        Message(
            Role=RoleAssistant,
            Content="calling",
            ToolCalls=(ToolCall(ID="call-1", Name="bash", Input='{"command":"pwd"}'),),
        ),
    ]
    # 2. tool results become ordinary tool messages.
    tool_results = [record for record in records if record.Type == store.EventToolResult]
    assert store.messagesFromRecords(tool_results) == [
        Message(Role=RoleTool, ToolCallID="call-1", ToolName="bash", Content="/tmp/workspace")
    ]
    # 3. compaction replaces the transcript with its own kept_messages.
    compact = next(record for record in records if record.Type == store.EventCompact)
    assert compact.Compact is not None
    assert store.messagesFromRecords([compact]) == list(compact.Compact.KeptMessages)
    # 4. reset keeps only the system messages that survived compaction.
    assert store.messagesFromRecords(records[:13]) == list(compact.Compact.KeptMessages)
    # 5. context replacement is authoritative; the final replay is exactly it.
    assert st.Messages(session_id) == [Message(Role=RoleSystem, Content="replaced")]

    summaries = st.List()
    assert [summary.ID for summary in summaries] == [
        store.SessionID("20260916T130000000000000"),
        session_id,
    ]
    assert summaries[1].Title == "fixture session"
    assert summaries[1].Provider == "deepseek"
    assert summaries[1].Model == "deepseek-reasoner"

    repository = store.NewRepository(st)
    messages, meta = repository.Load(session_key)
    assert messages == [Message(Role=RoleSystem, Content="replaced")]
    assert session_key == meta.ID
    assert meta.CWD == "/tmp/workspace"
    assert meta.ProjectID == "/tmp/workspace"
    assert meta.ConfigRoot == "/home/user"
    assert meta.InstructionSources == ("AGENTS.md", "/home/user/.superagent/AGENTS.md")
    assert meta.WorkspaceSpec is not None
    assert meta.WorkspaceSpec.PrimaryRoot == "/tmp/workspace"
    assert meta.WorkspaceSpec.Roots[0].Access == "read_write"

    audit = repository.LoadAuditEvents(session_key)
    assert [event.Type for event in audit] == [
        store.EventToolResult,
        store.EventApprovalDecision,
        store.EventError,
        store.EventCancel,
    ]
    assert audit[0].ToolCall is not None
    assert audit[0].ToolCall.Name == "bash"
    assert audit[0].Result == "/tmp/workspace"
    assert audit[1].Decision == "once"
    assert audit[2].Error == "something failed"

    assert st.LoadMemory() == ["remember this", "and this"]

    files, undo_messages, index = repository.LoadUndoPoint(session_key)
    assert index == 8
    assert undo_messages == store.messagesFromRecords(records[:8])
    assert len(undo_messages) == 5
    assert len(files) == 1
    assert files[0].Path == "a.txt"
    assert files[0].Exists is True
    assert files[0].Content == "old"
    assert files[0].Mode == 420

    # The fixture is a copy, so mutating it proves the store writes what it read.
    st.Append(
        session_id,
        store.Record(Type=store.EventMessageAppended, Message=Message(Role=RoleUser, Content="after replay")),
    )
    assert st.Messages(session_id)[-1].Content == "after replay"
