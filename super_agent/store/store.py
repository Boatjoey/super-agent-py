"""Durable session storage: one directory per session, one JSONL transcript.

The layout, JSON keys, enum strings, timestamp format, and file modes are a
persisted contract, so the store writes a stable byte shape even though byte
equality is not required:

* ``~/.superagent/sessions/<session-id>/meta.json`` — the sidecar the session is
  discovered by, written atomically and last, so an interrupted creation cannot
  leave an orphan that looks complete.
* ``~/.superagent/sessions/<session-id>/events.jsonl`` — the append-only log.
* ``~/.superagent/sessions/_memory.json`` — the cross-session memory.

Concurrency: a :class:`threading.Lock` guards every method. Every method is
synchronous, but the store is reached from the session's asynchronous code and
from the interactive CLI's own threads, and ``append`` performs a ``meta.json``
read-modify-write next to an append plus ``fsync``. The lock keeps those cycles
whole; a "no lock at all" argument could not show that.
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

from super_agent import jsonutil, timeutil
from super_agent.runtime.protocol.types import ROLE_SYSTEM, ROLE_TOOL, Message, ToolCall

#: The record kinds the log can hold. ``messages_from_records`` turns five of them
#: into model context; the rest exist for audit and never reach a model.
EVENT_SESSION_STARTED: Final[str] = "session_started"
EVENT_MESSAGE_APPENDED: Final[str] = "message_appended"
EVENT_APPROVAL_DECISION: Final[str] = "approval_decision"
EVENT_TOOL_RESULT: Final[str] = "tool_result"
EVENT_CANCEL: Final[str] = "cancel"
EVENT_RESET: Final[str] = "reset"
EVENT_ERROR: Final[str] = "error"
EVENT_CHECKPOINT: Final[str] = "checkpoint"
EVENT_COMPACT: Final[str] = "compact"
EVENT_CONTEXT_REPLACED: Final[str] = "context_replaced"

#: The zero time, which serialises as ``0001-01-01T00:00:00Z``. Kept as a real
#: value so a zero metadata timestamp round-trips exactly.
ZERO_TIME: Final[datetime] = datetime(1, 1, 1, tzinfo=UTC)

#: The per-line read cap of 20 MiB. A line beyond it is not a record this format
#: can hold, so it fails loudly.
MAX_EVENT_BYTES: Final[int] = 20 * 1024 * 1024


class SessionID(str):
    """Identity of one stored session. A ``str`` subclass so it serialises as its value."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"SessionID({str.__repr__(self)})"


class TurnID(str):
    """Identity of one turn. A ``str`` subclass so it serialises as its value."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"TurnID({str.__repr__(self)})"


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceRootSpec:
    """One root a session may reach, as stored. ``Access`` is ``read`` or ``read_write``."""

    path: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="path"))
    access: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="access"))


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    """The durable workspace description stored beside session metadata."""

    primary_root: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="primary_root"))
    cwd: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="cwd"))
    roots: tuple[WorkspaceRootSpec, ...] = dataclasses.field(default=(), metadata=jsonutil.json_field(name="roots"))


@dataclasses.dataclass(frozen=True, slots=True)
class Metadata:
    """What ``meta.json`` holds.

    Fields without ``omitempty`` are always written, because the reader expects
    them.
    """

    id: SessionID = dataclasses.field(default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="id"))
    title: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="title"))
    created_at: datetime = dataclasses.field(default=ZERO_TIME, metadata=jsonutil.json_field(name="created_at"))
    updated_at: datetime = dataclasses.field(default=ZERO_TIME, metadata=jsonutil.json_field(name="updated_at"))
    provider: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="provider"))
    model: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="model"))
    cwd: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="cwd"))
    instruction_fingerprint: str = dataclasses.field(
        default="", metadata=jsonutil.json_field(name="instruction_fingerprint")
    )
    instruction_sources: tuple[str, ...] = dataclasses.field(
        default=(), metadata=jsonutil.json_field(name="instruction_sources", omitempty=True)
    )
    current_turn_id: TurnID = dataclasses.field(
        default_factory=lambda: TurnID(""), metadata=jsonutil.json_field(name="current_turn_id")
    )
    parent_id: SessionID = dataclasses.field(
        default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="parent_id", omitempty=True)
    )
    project_id: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="project_id", omitempty=True))
    config_root: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="config_root", omitempty=True))
    workspace: WorkspaceSpec | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="workspace", omitempty=True)
    )


@dataclasses.dataclass(frozen=True, slots=True)
class FileSnapshot:
    """One file captured before a mutating tool call."""

    path: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="path"))
    exists: bool = dataclasses.field(default=False, metadata=jsonutil.json_field(name="exists"))
    content: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="content", omitempty=True))
    mode: int = dataclasses.field(default=0, metadata=jsonutil.json_field(name="mode", omitempty=True))


@dataclasses.dataclass(frozen=True, slots=True)
class Checkpoint:
    """A named set of file snapshots recorded before a write."""

    id: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="id"))
    files: tuple[FileSnapshot, ...] = dataclasses.field(default=(), metadata=jsonutil.json_field(name="files"))
    reason: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="reason"))


@dataclasses.dataclass(frozen=True, slots=True)
class Compact:
    """A compaction: the summary, what it replaced, and what survived."""

    summary: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="summary"))
    original_messages: tuple[Message, ...] = dataclasses.field(
        default=(), metadata=jsonutil.json_field(name="original_messages")
    )
    kept_messages: tuple[Message, ...] = dataclasses.field(
        default=(), metadata=jsonutil.json_field(name="kept_messages")
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Record:
    """One line of ``events.jsonl``.

    Presence of ``Message``, ``ToolCall``, ``Checkpoint``, ``Compact``, and
    ``messages`` depends on ``type``: those fields are optional and only the ones
    a given ``type`` needs are set.
    """

    type: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="type"))
    session_id: SessionID = dataclasses.field(
        default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="session_id")
    )
    turn_id: TurnID = dataclasses.field(
        default_factory=lambda: TurnID(""), metadata=jsonutil.json_field(name="turn_id", omitempty=True)
    )
    time: datetime = dataclasses.field(default=ZERO_TIME, metadata=jsonutil.json_field(name="time"))
    message: Message | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="message", omitempty=True)
    )
    tool_call: ToolCall | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="tool_call", omitempty=True)
    )
    decision: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="decision", omitempty=True))
    result: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="result", omitempty=True))
    error: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="error", omitempty=True))
    checkpoint: Checkpoint | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="checkpoint", omitempty=True)
    )
    compact: Compact | None = dataclasses.field(
        default=None, metadata=jsonutil.json_field(name="compact", omitempty=True)
    )
    messages: tuple[Message, ...] = dataclasses.field(
        default=(), metadata=jsonutil.json_field(name="messages", omitempty=True)
    )


@dataclasses.dataclass(frozen=True, slots=True)
class Summary:
    """One row of the session list."""

    id: SessionID = dataclasses.field(default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="id"))
    title: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="title"))
    updated_at: datetime = dataclasses.field(default=ZERO_TIME, metadata=jsonutil.json_field(name="updated_at"))
    provider: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="provider"))
    model: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="model"))
    cwd: str = dataclasses.field(default="", metadata=jsonutil.json_field(name="cwd"))
    parent_id: SessionID = dataclasses.field(
        default_factory=lambda: SessionID(""), metadata=jsonutil.json_field(name="parent_id", omitempty=True)
    )


def default_root() -> str:
    """``~/.superagent/sessions``, the directory every store is rooted at by default."""
    return os.path.join(os.path.expanduser("~"), ".superagent", "sessions")


def new(root: str) -> Store:
    """A store rooted at ``root``; the directory is created lazily."""
    return Store(root)


def open_default() -> Store:
    """A store at :func:`default_root`."""
    return Store(default_root())


def new_id(moment: timeutil.Timestamp) -> SessionID:
    """A session identifier: nine fractional digits, without the dot."""
    return SessionID(timeutil.format_id(moment, keep_dot=False))


def new_turn_id(moment: timeutil.Timestamp) -> TurnID:
    """A turn identifier: the same instant, but the fraction keeps its dot."""
    return TurnID(timeutil.format_id(moment, keep_dot=True))


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


def fingerprint(messages: Sequence[Message]) -> str:
    """A digest of the system messages, so a session's instructions can be identified."""
    digest = hashlib.sha256()
    for message in messages:
        if message.role != ROLE_SYSTEM:
            continue
        digest.update(message.content.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def messages_from_records(records: Sequence[Record]) -> list[Message]:
    """Rebuild the model context from a log.

    Only five record types produce messages: appended messages, tool results,
    reset (which keeps system messages), compaction (which keeps its own kept
    messages), and context replacement. Approvals, errors, checkpoints, and
    cancels are audit records and never enter the context.
    """
    messages: list[Message] = []
    for record in records:
        if record.type == EVENT_MESSAGE_APPENDED:
            if record.message is not None:
                messages.append(record.message)
        elif record.type == EVENT_TOOL_RESULT:
            if record.tool_call is not None:
                messages.append(
                    Message(
                        role=ROLE_TOOL,
                        content=record.result,
                        tool_call_id=record.tool_call.id,
                        tool_name=record.tool_call.name,
                    )
                )
        elif record.type == EVENT_RESET:
            messages = systemMessages(messages)
        elif record.type == EVENT_COMPACT:
            if record.compact is not None:
                messages = list(record.compact.kept_messages)
        elif record.type == EVENT_CONTEXT_REPLACED:
            messages = list(record.messages)
    return messages


def systemMessages(messages: Sequence[Message]) -> list[Message]:
    """Only the ``system`` messages, in order; the rule reset and replay share."""
    return [message for message in messages if message.role == ROLE_SYSTEM]


class Store:
    """The durable session store. See the module docstring for the lock rationale."""

    def __init__(self, root: str) -> None:
        self.root = root
        self._lock = threading.Lock()

    # --- creation and append -------------------------------------------------

    def create(self, meta: Metadata, messages: Sequence[Message]) -> Metadata:
        """Create a session, or remove the directory it started and raise.

        The transcript is written first and ``meta.json`` last: sessions become
        discoverable only once the metadata exists, so a partial failure removes
        the directory instead of leaving an orphan session behind.
        """
        with self._lock:
            moment = timeutil.now()
            if meta.id == "":
                meta = dataclasses.replace(meta, id=new_id(moment))
            validateID(meta.id)
            if _isZeroTime(meta.created_at):
                meta = dataclasses.replace(meta, created_at=moment)
            meta = dataclasses.replace(meta, updated_at=moment)
            if meta.title == "":
                meta = dataclasses.replace(meta, title="Untitled")
            if meta.instruction_fingerprint == "":
                meta = dataclasses.replace(meta, instruction_fingerprint=fingerprint(messages))
            self._ensure_dir(self._session_dir(meta.id))
            try:
                self._append_unlocked(meta.id, Record(type=EVENT_SESSION_STARTED))
                for message in messages:
                    self._append_unlocked(meta.id, Record(type=EVENT_MESSAGE_APPENDED, message=message))
                self._write_meta(meta)
            except Exception:
                self._remove_session_dir(meta.id)
                raise
            return meta

    def append(self, session_id: SessionID, record: Record) -> None:
        """Append one record, refreshing the session's ``updated_at`` best effort."""
        with self._lock:
            validateID(session_id)
            self._append_unlocked(session_id, record)

    def _append_unlocked(self, session_id: SessionID, record: Record) -> None:
        if record.type == "":
            raise ValueError("record type is required")
        try:
            meta = self._metadata_unlocked(session_id)
        except FileNotFoundError:
            pass
        else:
            record = dataclasses.replace(
                record,
                session_id=session_id,
                turn_id=record.turn_id if record.turn_id != "" else meta.current_turn_id,
            )
            # Best effort: the UpdatedAt refresh is cosmetic, and refusing to
            # append the record over a failed refresh would lose the transcript
            # entry the record exists for.
            with contextlib.suppress(Exception):
                self._write_meta(dataclasses.replace(meta, updated_at=timeutil.now()))
        if record.session_id == "":
            record = dataclasses.replace(record, session_id=session_id)
        if _isZeroTime(record.time):
            record = dataclasses.replace(record, time=timeutil.now())
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

    def metadata(self, session_id: SessionID) -> Metadata:
        """Read one session's ``meta.json``."""
        with self._lock:
            validateID(session_id)
            return self._metadata_unlocked(session_id)

    def _metadata_unlocked(self, session_id: SessionID) -> Metadata:
        with open(self._meta_path(session_id), encoding="utf-8") as handle:
            return jsonutil.loads(handle.read(), Metadata)

    def set_current_turn(self, session_id: SessionID, turn: TurnID) -> None:
        """Point the session at a fresh turn id."""
        with self._lock:
            validateID(session_id)
            meta = self._metadata_unlocked(session_id)
            meta = dataclasses.replace(meta, current_turn_id=turn, updated_at=timeutil.now())
            self._write_meta(meta)

    def save_workspace_description(self, session_id: SessionID, spec: WorkspaceSpec) -> None:
        """Persist the durable workspace description and its canonical cwd.

        Only those fields of the metadata are rewritten, so the one-time upgrade
        from legacy metadata cannot disturb unrelated values.
        """
        with self._lock:
            meta = self._metadata_unlocked(session_id)
            cloned = WorkspaceSpec(primary_root=spec.primary_root, cwd=spec.cwd, roots=tuple(spec.roots))
            meta = dataclasses.replace(meta, workspace=cloned, cwd=spec.cwd, updated_at=timeutil.now())
            self._write_meta(meta)

    def rename_session(self, session_id: SessionID, title: str) -> None:
        """Change a session's title, rejecting an empty one."""
        with self._lock:
            validateID(session_id)
            title = title.strip()
            if title == "":
                raise ValueError("title is required")
            meta = self._metadata_unlocked(session_id)
            meta = dataclasses.replace(meta, title=title, updated_at=timeutil.now())
            self._write_meta(meta)

    def delete(self, session_id: SessionID) -> None:
        """Remove a session directory and everything in it."""
        with self._lock:
            validateID(session_id)
            self._remove_session_dir(session_id)

    def list(self) -> list[Summary]:
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
                        id=meta.id,
                        title=meta.title,
                        updated_at=meta.updated_at,
                        provider=meta.provider,
                        model=meta.model,
                        cwd=meta.cwd,
                        parent_id=meta.parent_id,
                    )
                )
            summaries.sort(key=lambda summary: summary.updated_at, reverse=True)
            return summaries

    # --- the event log -------------------------------------------------------

    def records(self, session_id: SessionID) -> list[Record]:
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

    def messages(self, session_id: SessionID) -> list[Message]:
        """The model context rebuilt from the log."""
        with self._lock:
            return messages_from_records(self._records_unlocked(session_id))

    def last_checkpoint(self, session_id: SessionID) -> Checkpoint | None:
        """The most recent checkpoint with files, or ``None``."""
        try:
            checkpoint, _messages, _index = self.checkpoint_undo(session_id)
        except FileNotFoundError:
            return None
        return checkpoint

    def checkpoint_undo(self, session_id: SessionID) -> tuple[Checkpoint, list[Message], int]:
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
                    record.type == EVENT_CHECKPOINT
                    and record.checkpoint is not None
                    and len(record.checkpoint.files) > 0
                ):
                    return record.checkpoint, messages_from_records(records[:index]), index
            raise FileNotFoundError("no checkpoint to undo")

    def truncate_after(self, session_id: SessionID, keep: int) -> None:
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

    def load_memory(self) -> list[str]:
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

    def save_memory(self, items: Sequence[str]) -> None:
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
        validateID(meta.id)
        self._ensure_dir(self._session_dir(meta.id))
        content = (jsonutil.dumps(meta, indent=2) + "\n").encode("utf-8")
        self._atomic_write(self._meta_path(meta.id), content, ".meta-", ".json")

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

        ``os.makedirs`` would apply ``mode`` to the leaf only, so the mode is set
        on every directory this creates; the difference is visible to a test that
        checks the sessions root's mode.
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


def _isZeroTime(moment: datetime) -> bool:
    """True for the value this module writes as the zero time."""
    return moment == ZERO_TIME
