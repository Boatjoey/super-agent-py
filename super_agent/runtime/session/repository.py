"""The outbound persistence and filesystem ports.

``runtime/session`` never touches ``os`` or ``pathlib``: every durable read and
write goes through :class:`Repository`, and every filesystem decision goes through
:class:`Workspace`. R5 in ``tests/architecture/test_dependencies.py`` enforces it,
which is what keeps the use-case layer testable without a real disk.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Final, Protocol

from super_agent.jsonutil import json_field
from super_agent.runtime.machine import ApprovalDecision, Message, ToolCall


class SessionID(str):
    """Identity of one stored session."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"SessionID({str.__repr__(self)})"


class WorkspaceAccessMode(str):
    """Whether a workspace root may be written to."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"WorkspaceAccessMode({str.__repr__(self)})"


WORKSPACE_ACCESS_READ: Final[WorkspaceAccessMode] = WorkspaceAccessMode("read")
WORKSPACE_ACCESS_READ_WRITE: Final[WorkspaceAccessMode] = WorkspaceAccessMode("read_write")


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceRootSpec:
    """One root a session may reach, and how."""

    path: str = ""
    access: WorkspaceAccessMode = WORKSPACE_ACCESS_READ


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    """Durable session data describing the workspace a session was saved with.

    Filesystem validation and access decisions belong to the concrete
    :class:`Workspace` implementation, not here.
    """

    primary_root: str = ""
    cwd: str = ""
    roots: tuple[WorkspaceRootSpec, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class Metadata:
    """What the session knows about itself, including what persistence needs."""

    id: SessionID = dataclasses.field(default_factory=lambda: SessionID(""))
    title: str = ""
    provider: str = ""
    model: str = ""
    cwd: str = ""
    instruction_sources: tuple[str, ...] = ()
    parent_id: SessionID = dataclasses.field(default_factory=lambda: SessionID(""))
    project_id: str = ""
    config_root: str = ""
    workspace_spec: WorkspaceSpec | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Summary:
    """One row of the session list."""

    id: SessionID = dataclasses.field(default_factory=lambda: SessionID(""))
    title: str = ""
    updated_at: datetime | None = None
    provider: str = ""
    model: str = ""
    cwd: str = ""
    parent_id: SessionID = dataclasses.field(default_factory=lambda: SessionID(""))


@dataclasses.dataclass(frozen=True, slots=True)
class FileSnapshot:
    """One file captured before a mutating tool call."""

    path: str = ""
    exists: bool = False
    content: str = ""
    mode: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class AuditEvent:
    """One auditable decision or failure. Never sent to a model."""

    type: str = dataclasses.field(default="", metadata=json_field(name="type"))
    time: datetime | None = dataclasses.field(default=None, metadata=json_field(name="time"))
    tool_call: ToolCall | None = dataclasses.field(default=None, metadata=json_field(name="tool_call", omitempty=True))
    decision: str = dataclasses.field(default="", metadata=json_field(name="decision", omitempty=True))
    result: str = dataclasses.field(default="", metadata=json_field(name="result", omitempty=True))
    error: str = dataclasses.field(default="", metadata=json_field(name="error", omitempty=True))


def workspaceSpecPointer(spec: WorkspaceSpec) -> WorkspaceSpec:
    """A copy of ``spec``, so a caller cannot mutate what was persisted."""
    return WorkspaceSpec(primary_root=spec.primary_root, cwd=spec.cwd, roots=tuple(spec.roots))


class Repository(Protocol):
    """The outbound persistence port used by the session's use cases.

    Implementations decide how metadata, transcripts, and checkpoints are stored;
    the session layer only decides *when* each of them must be durable.
    """

    def create(self, metadata: Metadata, initial: list[Message]) -> Metadata:
        """Create a new session, or raise."""
        ...

    def assign_new_turn_id(self, session_id: SessionID) -> None: ...

    def save_message(self, session_id: SessionID, message: Message) -> None: ...

    def save_approval(self, session_id: SessionID, decision: ApprovalDecision, call: ToolCall | None) -> None: ...

    def save_error(self, session_id: SessionID, error: BaseException) -> None: ...

    def save_cancel(self, session_id: SessionID) -> None: ...

    def save_reset(self, session_id: SessionID) -> None: ...

    def save_conversation_replacement(self, session_id: SessionID, messages: list[Message]) -> None: ...

    def save_compaction(
        self, session_id: SessionID, summary: str, original: list[Message], kept: list[Message]
    ) -> None: ...

    def save_checkpoint(self, session_id: SessionID, call: ToolCall, files: list[FileSnapshot]) -> None: ...

    def list(self) -> list[Summary]: ...

    def load(self, session_id: SessionID) -> tuple[list[Message], Metadata]: ...

    def load_audit_events(self, session_id: SessionID) -> list[AuditEvent]: ...

    def rename_session(self, session_id: SessionID, title: str) -> None: ...

    def delete(self, session_id: SessionID) -> None: ...

    def load_undo_point(self, session_id: SessionID) -> tuple[list[FileSnapshot], list[Message], int]:
        """The files of the most recent non-empty checkpoint, the transcript as of
        that checkpoint, and the record index for :meth:`TruncateAfter`."""
        ...

    def truncate_after(self, session_id: SessionID, index: int) -> None:
        """Drop every record after ``index``, keeping the checkpoint record itself."""
        ...

    def load_memory(self) -> list[str]: ...

    def save_memory(self, items: list[str]) -> None: ...

    def save_workspace_description(self, session_id: SessionID, spec: WorkspaceSpec) -> None:
        """Persist the durable workspace description and its canonical cwd without
        touching unrelated metadata.

        Resume uses it to upgrade legacy metadata to canonical ``WorkspaceSpec``
        form exactly once.
        """
        ...


class Workspace(Protocol):
    """The outbound filesystem port used by checkpoints, attachments, and exports."""

    def spec(self) -> WorkspaceSpec: ...

    def validate(self, spec: WorkspaceSpec) -> None: ...

    def canonicalize(self, spec: WorkspaceSpec) -> WorkspaceSpec:
        """Re-resolve every path in ``spec`` against the current filesystem.

        Resume uses it once to upgrade legacy metadata, whose saved cwd never
        promised a canonical path; new specs stay strict.
        """
        ...

    def activate(self, spec: WorkspaceSpec) -> None: ...

    def capture(self, paths: list[str]) -> list[FileSnapshot]: ...

    def restore(self, files: list[FileSnapshot]) -> None: ...
