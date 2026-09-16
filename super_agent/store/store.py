"""Durable session storage: one directory per session, one JSONL transcript.

Ported from ``store/store.go``. The layout, JSON keys, enum strings, timestamp
format, and file modes are a contract with the Go implementation, so the store
writes the same bytes shape even though byte equality is not required:

* ``~/.superagent/sessions/<session-id>/meta.json`` — the sidecar the session is
  discovered by, written atomically and last, so an interrupted creation cannot
  leave an orphan that looks complete.
* ``~/.superagent/sessions/<session-id>/events.jsonl`` — the append-only log.
* ``~/.superagent/sessions/_memory.json`` — the cross-session memory.

Concurrency: Go's ``sync.Mutex`` becomes a :class:`threading.Lock` here. Every
public method is synchronous, but the store is reached from the session's
asynchronous code and from the TUI's own threads, and ``Append`` performs a
``meta.json`` read-modify-write next to an append plus ``fsync``. A lock keeps
those cycles whole, which is what Go's mutex exists for and what a "no lock at
all" argument could not show.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
import os
import shutil
import tempfile
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Final, cast

from super_agent import jsonutil
from super_agent.runtime.protocol.types import Message, RoleSystem, RoleTool, ToolCall

#: The record kinds the log can hold. ``messagesFromRecords`` turns five of them
#: into model context; the rest exist for audit and never reach a model.
EventSessionStarted: Final[str] = "session_started"
EventMessageAppended: Final[str] = "message_appended"
EventApprovalDecision: Final[str] = "approval_decision"
EventToolResult: Final[str] = "tool_result"
EventCancel: Final[str] = "cancel"
EventReset: Final[str] = "reset"
EventError: Final[str] = "error"
EventCheckpoint: Final[str] = "checkpoint"
EventCompact: Final[str] = "compact"
EventContextReplaced: Final[str] = "context_replaced"

#: Go's ``time.Time{}``, which marshals as ``0001-01-01T00:00:00Z``. Kept as a
#: real value so a zero metadata timestamp round-trips exactly.
ZERO_TIME: Final[datetime] = datetime(1, 1, 1, tzinfo=UTC)

#: The per-line read cap, mirroring Go's ``bufio.Scanner`` buffer of 20 MiB. A
#: line beyond it is not a record this format can hold, so it fails loudly.
MAX_EVENT_BYTES: Final[int] = 20 * 1024 * 1024


class SessionID(str):
    """Identity of one stored session. Mirrors Go's string-backed ``SessionID``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"SessionID({str.__repr__(self)})"


class TurnID(str):
    """Identity of one turn. Mirrors Go's string-backed ``TurnID``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"TurnID({str.__repr__(self)})"


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceRootSpec:
    """One root a session may reach, as stored. ``Access`` is ``read`` or ``read_write``."""

    Path: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="path"))
    Access: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="access"))


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    """The durable workspace description stored beside session metadata."""

    PrimaryRoot: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="primary_root"))
    CWD: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="cwd"))
    Roots: tuple[WorkspaceRootSpec, ...] = dataclasses.field(default=(), metadata=jsonutil.json_field(name="roots"))


@dataclasses.dataclass(frozen=True, slots=True)
class Metadata:
    """What ``meta.json`` holds.

    Fields without ``omitempty`` are always written, exactly as Go's struct tags
    dictate, because the Go reader expects them.
    """

    ID: SessionID = dataclasses.field(default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="id"))
    Title: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="title"))
    CreatedAt: datetime = dataclasses.field(default=ZERO_TIME, metadata=jsonutil.json_field(name="created_at"))
    UpdatedAt: datetime = dataclasses.field(default=ZERO_TIME, metadata=jsonutil.json_field(name="updated_at"))
    Provider: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="provider"))
    Model: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="model"))
    CWD: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="cwd"))
    InstructionFingerprint: str = dataclasses.field(
        default="", metadata=jsonutil.json_field(name="instruction_fingerprint")
    )
    InstructionSources: tuple[str, ...] = dataclasses.field(
        default=(), metadata=jsonutil.json_field(name="instruction_sources", omitempty=True)
    )
    CurrentTurnID: TurnID = dataclasses.field(
        default_factory=lambda: TurnID(""), metadata=jsonutil.json_field(name="current_turn_id")
    )
    ParentID: SessionID = dataclasses.field(
        default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="parent_id", omitempty=True)
    )
    ProjectID: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="project_id", omitempty=True))
    ConfigRoot: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="config_root", omitempty=True))
    Workspace: WorkspaceSpec | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="workspace", omitempty=True)
    )


@dataclasses.dataclass(frozen=True, slots=True)
class FileSnapshot:
    """One file captured before a mutating tool call."""

    Path: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="path"))
    Exists: bool = dataclasses.field(default=False, metadata=jsonutil.json_field(name="exists"))
    Content: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="content", omitempty=True))
    Mode: int = dataclasses.field(default=0, metadata=jsonutil.json_field(name="mode", omitempty=True))


@dataclasses.dataclass(frozen=True, slots=True)
class Checkpoint:
    """A named set of file snapshots recorded before a write."""

    ID: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="id"))
    Files: tuple[FileSnapshot, ...] = dataclasses.field(default=(), metadata=jsonutil.json_field(name="files"))
    Reason: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="reason"))


@dataclasses.dataclass(frozen=True, slots=True)
class Compact:
    """A compaction: the summary, what it replaced, and what survived."""

    Summary: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="summary"))
    OriginalMessages: tuple[Message, ...] = dataclasses.field(
        default=(), metadata=jsonutil.json_field(name="original_messages")
    )
    KeptMessages: tuple[Message, ...] = dataclasses.field(
        default=(), metadata=jsonutil.json_field(name="kept_messages")
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Record:
    """One line of ``events.jsonl``.

    Presence of ``Message``, ``ToolCall``, ``Checkpoint``, ``Compact``, and
    ``Messages`` depends on ``Type``; Go encodes that with pointers and slices,
    so the Python fields are optional exactly the same way.
    """

    Type: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="type"))
    SessionID: SessionID = dataclasses.field(
        default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="session_id")
    )
    TurnID: TurnID = dataclasses.field(
        default_factory=lambda: TurnID(""), metadata=jsonutil.json_field(name="turn_id", omitempty=True)
    )
    Time: datetime = dataclasses.field(default=ZERO_TIME, metadata=jsonutil.json_field(name="time"))
    Message: Message | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="message", omitempty=True)
    )
    ToolCall: ToolCall | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="tool_call", omitempty=True)
    )
    Decision: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="decision", omitempty=True))
    Result: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="result", omitempty=True))
    Error: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="error", omitempty=True))
    Checkpoint: Checkpoint | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="checkpoint", omitempty=True)
    )
    Compact: Compact | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="compact", omitempty=True)
    )
    Messages: tuple[Message, ...] = dataclasses.field(
        default=(), metadata=jsonutil.json_field(name="messages", omitempty=True)
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Summary:
    """One row of the session list."""

    ID: SessionID = dataclasses.field(default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="id"))
    Title: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="title"))
    UpdatedAt: datetime = dataclasses.field(default=ZERO_TIME, metadata=jsonutil.json_field(name="updated_at"))
    Provider: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="provider"))
    Model: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="model"))
    CWD: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="cwd"))
    ParentID: SessionID = dataclasses.field(
        default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="parent_id", omitempty=True)
    )


def DefaultRoot() -> str:
    """``~/.superagent/sessions``, the directory every store is rooted at by default."""
    return os.path.join(os.path.expanduser("~"), ".superagent", "sessions")


def New(root: str) -> Store:
    """A store rooted at ``root``; the directory is created lazily."""
    return Store(root)


def OpenDefault() -> Store:
    """A store at :func:`DefaultRoot`."""
    return Store(DefaultRoot())


def NewID(now: datetime) -> SessionID:
    """A session identifier: nine fractional digits, without the dot."""
    return SessionID(_idText(now, keep_dot=False))


def NewTurnID(now: datetime) -> TurnID:
    """A turn identifier: the same instant, but the fraction keeps its dot."""
    return TurnID(_idText(now, keep_dot=True))


def validateID(session_id: SessionID) -> None:
    """Reject session identifiers that could escape the sessions root.

    Every identifier is joined into filesystem paths, so separators, dot
    segments, and reserved names must never reach the store: a user-supplied id
    from a resume or a delete resolves to a directory here unchecked otherwise.
    """
    name = str(session_id)
    if name == "":
        raise ValueError("session id is required")
    if name in {".", ".."} or name != os.path.basename(name) or name.startswith("_"):
        raise ValueError("invalid session id: " + name)
    for character in name:
        if "0" <= character <= "9" or "a" <= character <= "z" or "A" <= character <= "Z" or character in "-_":
            continue
        raise ValueError("invalid session id: " + name)


def Fingerprint(messages: Sequence[Message]) -> str:
    """A digest of the system messages, so a session's instructions can be identified."""
    digest = hashlib.sha256()
    for message in messages:
        if message.Role != RoleSystem:
            continue
        digest.update(message.Content.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def messagesFromRecords(records: Sequence[Record]) -> list[Message]:
    """Rebuild the model context from a log.

    Only five record types produce messages: appended messages, tool results,
    reset (which keeps system messages), compaction (which keeps its own kept
    messages), and context replacement. Approvals, errors, checkpoints, and
    cancels are audit records and never enter the context.
    """
    messages: list[Message] = []
    for record in records:
        if record.Type == EventMessageAppended:
            if record.Message is not None:
                messages.append(record.Message)
        elif record.Type == EventToolResult:
            if record.ToolCall is not None:
                messages.append(
                    Message(
                        Role=RoleTool,
                        Content=record.Result,
                        ToolCallID=record.ToolCall.ID,
                        ToolName=record.ToolCall.Name,
                    )
                )
        elif record.Type == EventReset:
            messages = systemMessages(messages)
        elif record.Type == EventCompact:
            if record.Compact is not None:
                messages = list(record.Compact.KeptMessages)
        elif record.Type == EventContextReplaced:
            messages = list(record.Messages)
    return messages


def systemMessages(messages: Sequence[Message]) -> list[Message]:
    """Only the ``system`` messages, in order; the rule reset and replay share."""
    return [message for message in messages if message.Role == RoleSystem]


class Store:
    """The durable session store. See the module docstring for the lock rationale."""

    def __init__(self, root: str) -> None:
        self.root = root
        self._lock = threading.Lock()

    # --- creation and append -------------------------------------------------

    def Create(self, meta: Metadata, messages: Sequence[Message]) -> Metadata:
        """Create a session, or remove the directory it started and raise.

        The transcript is written first and ``meta.json`` last: sessions become
        discoverable only once the metadata exists, so a partial failure removes
        the directory instead of leaving an orphan session behind.
        """
        with self._lock:
            if meta.ID == "":
                meta = dataclasses.replace(meta, ID=NewID(datetime.now(UTC)))
            validateID(meta.ID)
            now = datetime.now(UTC)
            if _isZeroTime(meta.CreatedAt):
                meta = dataclasses.replace(meta, CreatedAt=now)
            meta = dataclasses.replace(meta, UpdatedAt=now)
            if meta.Title == "":
                meta = dataclasses.replace(meta, Title="Untitled")
            if meta.InstructionFingerprint == "":
                meta = dataclasses.replace(meta, InstructionFingerprint=Fingerprint(messages))
            self._ensure_dir(self._session_dir(meta.ID))
            try:
                self._append_unlocked(meta.ID, Record(Type=EventSessionStarted))
                for message in messages:
                    self._append_unlocked(meta.ID, Record(Type=EventMessageAppended, Message=message))
                self._write_meta(meta)
            except Exception:
                self._remove_session_dir(meta.ID)
                raise
            return meta

    def Append(self, session_id: SessionID, record: Record) -> None:
        """Append one record, refreshing the session's ``UpdatedAt`` best effort."""
        with self._lock:
            validateID(session_id)
            self._append_unlocked(session_id, record)

    def _append_unlocked(self, session_id: SessionID, record: Record) -> None:
        if record.Type == "":
            raise ValueError("record type is required")
        try:
            meta = self._metadata_unlocked(session_id)
        except FileNotFoundError:
            pass
        else:
            record = dataclasses.replace(
                record,
                SessionID=session_id,
                TurnID=record.TurnID if record.TurnID != "" else meta.CurrentTurnID,
            )
            # Best effort: the UpdatedAt refresh is cosmetic, and refusing to
            # append the record over a failed refresh would lose the transcript
            # entry the record exists for.
            with contextlib.suppress(Exception):
                self._write_meta(dataclasses.replace(meta, UpdatedAt=datetime.now(UTC)))
        if record.SessionID == "":
            record = dataclasses.replace(record, SessionID=session_id)
        if _isZeroTime(record.Time):
            record = dataclasses.replace(record, Time=datetime.now(UTC))
        self._ensure_dir(self._session_dir(session_id))
        encoded = (jsonutil.dumps(record) + "\n").encode("utf-8")
        descriptor = os.open(self._events_path(session_id), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, encoded)
            # Sync every append: a torn tail line would otherwise make the whole
            # transcript unreadable after a crash or power loss.
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    # --- metadata ------------------------------------------------------------

    def Metadata(self, session_id: SessionID) -> Metadata:
        """Read one session's ``meta.json``."""
        with self._lock:
            validateID(session_id)
            return self._metadata_unlocked(session_id)

    def _metadata_unlocked(self, session_id: SessionID) -> Metadata:
        with open(self._meta_path(session_id), encoding="utf-8") as handle:
            return jsonutil.loads(handle.read(), Metadata)

    def SetCurrentTurn(self, session_id: SessionID, turn: TurnID) -> None:
        """Point the session at a fresh turn id."""
        with self._lock:
            validateID(session_id)
            meta = self._metadata_unlocked(session_id)
            meta = dataclasses.replace(meta, CurrentTurnID=turn, UpdatedAt=datetime.now(UTC))
            self._write_meta(meta)

    def SaveWorkspaceDescription(self, session_id: SessionID, spec: WorkspaceSpec) -> None:
        """Persist the durable workspace description and its canonical cwd.

        Only those fields of the metadata are rewritten, so the one-time upgrade
        from legacy metadata cannot disturb unrelated values.
        """
        with self._lock:
            meta = self._metadata_unlocked(session_id)
            cloned = WorkspaceSpec(PrimaryRoot=spec.PrimaryRoot, CWD=spec.CWD, Roots=tuple(spec.Roots))
            meta = dataclasses.replace(meta, Workspace=cloned, CWD=spec.CWD, UpdatedAt=datetime.now(UTC))
            self._write_meta(meta)

    def RenameSession(self, session_id: SessionID, title: str) -> None:
        """Change a session's title, rejecting an empty one."""
        with self._lock:
            validateID(session_id)
            title = title.strip()
            if title == "":
                raise ValueError("title is required")
            meta = self._metadata_unlocked(session_id)
            meta = dataclasses.replace(meta, Title=title, UpdatedAt=datetime.now(UTC))
            self._write_meta(meta)

    def Delete(self, session_id: SessionID) -> None:
        """Remove a session directory and everything in it."""
        with self._lock:
            validateID(session_id)
            self._remove_session_dir(session_id)

    def List(self) -> list[Summary]:
        """Every discoverable session, most recently updated first."""
        with self._lock:
            try:
                names = sorted(os.listdir(self.root))
            except FileNotFoundError:
                return []
            summaries: list[Summary] = []
            for name in names:
                if not os.path.isdir(os.path.join(self.root, name)):
                    continue
                try:
                    meta = self._metadata_unlocked(SessionID(name))
                except (OSError, ValueError, TypeError):
                    continue
                summaries.append(
                    Summary(
                        ID=meta.ID,
                        Title=meta.Title,
                        UpdatedAt=meta.UpdatedAt,
                        Provider=meta.Provider,
                        Model=meta.Model,
                        CWD=meta.CWD,
                        ParentID=meta.ParentID,
                    )
                )
            summaries.sort(key=lambda summary: summary.UpdatedAt, reverse=True)
            return summaries

    # --- the event log -------------------------------------------------------

    def Records(self, session_id: SessionID) -> list[Record]:
        """Every record in the log, after healing a torn tail."""
        with self._lock:
            validateID(session_id)
            return self._records_unlocked(session_id)

    def _records_unlocked(self, session_id: SessionID) -> list[Record]:
        """Read the event log, healing a torn final line.

        A torn final line — the signature of a crash mid-append — is healed by
        truncating back to the last complete record; corruption anywhere else
        fails loudly, because silently dropping mid-file records would rewrite
        history.
        """
        path = self._events_path(session_id)
        with open(path, "rb") as handle:
            raw = handle.read()
        records: list[Record] = []
        good = 0
        lines = raw.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        for line in lines:
            if len(line) > MAX_EVENT_BYTES:
                raise ValueError(f"session event at byte {good} exceeds the {MAX_EVENT_BYTES} byte read cap")
            try:
                record = jsonutil.loads(line.decode("utf-8"), Record)
            except (UnicodeDecodeError, ValueError, TypeError) as error:
                offset = good + len(line) + 1
                if raw[offset:].strip() == b"":
                    os.truncate(path, good)
                    return records
                raise ValueError(f"corrupt session event at byte {good}: {error}") from error
            records.append(record)
            good += len(line) + 1
        return records

    def Messages(self, session_id: SessionID) -> list[Message]:
        """The model context rebuilt from the log."""
        with self._lock:
            return messagesFromRecords(self._records_unlocked(session_id))

    def LastCheckpoint(self, session_id: SessionID) -> Checkpoint | None:
        """The most recent checkpoint with files, or ``None``."""
        try:
            checkpoint, _messages, _index = self.CheckpointUndo(session_id)
        except FileNotFoundError:
            return None
        return checkpoint

    def CheckpointUndo(self, session_id: SessionID) -> tuple[Checkpoint, list[Message], int]:
        """The newest checkpoint with files, the transcript as of it, and its index.

        Checkpoints without files (tools that do not track paths) are skipped so
        undo always has something to restore.
        """
        with self._lock:
            validateID(session_id)
            records = self._records_unlocked(session_id)
            for index in range(len(records) - 1, -1, -1):
                record = records[index]
                if (
                    record.Type == EventCheckpoint
                    and record.Checkpoint is not None
                    and len(record.Checkpoint.Files) > 0
                ):
                    return record.Checkpoint, messagesFromRecords(records[:index]), index
            raise FileNotFoundError("no checkpoint to undo")

    def TruncateAfter(self, session_id: SessionID, keep: int) -> None:
        """Drop every record after ``keep``, keeping the record at ``keep`` itself.

        The rewrite goes through a temporary file so a failed write cannot corrupt
        the transcript.
        """
        with self._lock:
            validateID(session_id)
            records = self._records_unlocked(session_id)
            if keep < 0 or keep >= len(records) - 1:
                return
            target = self._events_path(session_id)
            temporary = target + ".tmp"
            try:
                writeRecords(temporary, records[: keep + 1])
                os.replace(temporary, target)
            except OSError:
                with contextlib.suppress(OSError):
                    os.remove(temporary)
                raise

    # --- cross-session memory ------------------------------------------------

    def LoadMemory(self) -> list[str]:
        """The cross-session memory list, empty when the file does not exist."""
        with self._lock:
            try:
                with open(self._memory_path(), encoding="utf-8") as handle:
                    content = handle.read()
            except FileNotFoundError:
                return []
            data: Any = json.loads(content)
            if not isinstance(data, list):
                raise TypeError(f"memory must be a JSON array, got {type(data).__name__}")
            return [str(item) for item in cast("list[Any]", data)]

    def SaveMemory(self, items: Sequence[str]) -> None:
        """Replace the cross-session memory list atomically."""
        with self._lock:
            self._ensure_dir(self.root)
            content = (jsonutil.dumps(list(items), indent=2) + "\n").encode("utf-8")
            self._atomic_write(self._memory_path(), content, ".memory-", ".json")

    # --- paths and atomic writes ---------------------------------------------

    def _session_dir(self, session_id: SessionID) -> str:
        return os.path.join(self.root, str(session_id))

    def _meta_path(self, session_id: SessionID) -> str:
        return os.path.join(self._session_dir(session_id), "meta.json")

    def _events_path(self, session_id: SessionID) -> str:
        return os.path.join(self._session_dir(session_id), "events.jsonl")

    def _memory_path(self) -> str:
        return os.path.join(self.root, "_memory.json")

    def _write_meta(self, meta: Metadata) -> None:
        """Write ``meta.json`` atomically, mode ``0600``, two-space indented.

        ``meta.json`` gates session discovery and every further append, so a torn
        write must never leave a half-written file behind: write, fsync, and
        atomically rename a temporary file.
        """
        validateID(meta.ID)
        self._ensure_dir(self._session_dir(meta.ID))
        content = (jsonutil.dumps(meta, indent=2) + "\n").encode("utf-8")
        self._atomic_write(self._meta_path(meta.ID), content, ".meta-", ".json")

    def _atomic_write(self, path: str, content: bytes, prefix: str, suffix: str) -> None:
        """Same-directory temp file, mode ``0600``, fsync, then ``os.replace``."""
        directory = os.path.dirname(path)
        descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=prefix, suffix=suffix)
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(temporary)
            raise

    def _ensure_dir(self, path: str) -> None:
        """Create ``path`` and any missing parent, mode ``0700`` for every level.

        ``os.makedirs`` applies ``mode`` to the leaf only, while Go's ``MkdirAll``
        applies it to every directory it creates; the difference is visible to a
        test that checks the sessions root's mode.
        """
        missing: list[str] = []
        current = os.path.normpath(path)
        while not os.path.isdir(current):
            missing.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        for directory in reversed(missing):
            os.mkdir(directory, 0o700)
        with contextlib.suppress(OSError):
            os.chmod(path, 0o700)

    def _remove_session_dir(self, session_id: SessionID) -> None:
        shutil.rmtree(self._session_dir(session_id), ignore_errors=True)


def writeRecords(path: str, records: Sequence[Record]) -> None:
    """Rewrite a whole log, mode ``0600``, fsynced before the caller renames it."""
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        for record in records:
            os.write(descriptor, (jsonutil.dumps(record) + "\n").encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _idText(moment: datetime, *, keep_dot: bool) -> str:
    """Go's ``20060102T150405.000000000`` layout, from seconds and microseconds.

    ``strftime("%f")`` only reaches microseconds, so the nine-digit fraction is
    assembled by hand: Python has no sub-microsecond clock here, and the last
    three digits are zeros rather than Go's nanoseconds.
    """
    utc = moment.astimezone(UTC)
    stamp = f"{utc.year:04d}{utc.month:02d}{utc.day:02d}T{utc.hour:02d}{utc.minute:02d}{utc.second:02d}"
    fraction = f"{utc.microsecond:06d}000"
    return f"{stamp}.{fraction}" if keep_dot else f"{stamp}{fraction}"


def _isZeroTime(moment: datetime) -> bool:
    """Go's ``time.Time.IsZero`` for the value this module writes as the zero time."""
    return moment == ZERO_TIME
