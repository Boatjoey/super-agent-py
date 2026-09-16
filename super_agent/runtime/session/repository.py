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


WorkspaceAccessRead: Final[WorkspaceAccessMode] = WorkspaceAccessMode("read")
WorkspaceAccessReadWrite: Final[WorkspaceAccessMode] = WorkspaceAccessMode("read_write")


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceRootSpec:
    """One root a session may reach, and how."""

    Path: str = ""
    Access: WorkspaceAccessMode = WorkspaceAccessRead


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    """Durable session data describing the workspace a session was saved with.

    Filesystem validation and access decisions belong to the concrete
    :class:`Workspace` implementation, not here.
    """

    PrimaryRoot: str = ""
    CWD: str = ""
    Roots: tuple[WorkspaceRootSpec, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class Metadata:
    """What the session knows about itself, including what persistence needs."""

    ID: SessionID = dataclasses.field(default_factory=lambda: SessionID(""))
    Title: str = ""
    Provider: str = ""
    Model: str = ""
    CWD: str = ""
    InstructionSources: tuple[str, ...] = ()
    ParentID: SessionID = dataclasses.field(default_factory=lambda: SessionID(""))
    ProjectID: str = ""
    ConfigRoot: str = ""
    WorkspaceSpec: WorkspaceSpec | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class Summary:
    """One row of the session list."""

    ID: SessionID = dataclasses.field(default_factory=lambda: SessionID(""))
    Title: str = ""
    UpdatedAt: datetime | None = None
    Provider: str = ""
    Model: str = ""
    CWD: str = ""
    ParentID: SessionID = dataclasses.field(default_factory=lambda: SessionID(""))


@dataclasses.dataclass(frozen=True, slots=True)
class FileSnapshot:
    """One file captured before a mutating tool call."""

    Path: str = ""
    Exists: bool = False
    Content: str = ""
    Mode: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class AuditEvent:
    """One auditable decision or failure. Never sent to a model."""

    Type: str = dataclasses.field(default="", metadata=json_field(name="type"))
    Time: datetime | None = dataclasses.field(default=None, metadata=json_field(name="time"))
    ToolCall: ToolCall | None = dataclasses.field(default=None, metadata=json_field(name="tool_call", omitempty=True))
    Decision: str = dataclasses.field(default="", metadata=json_field(name="decision", omitempty=True))
    Result: str = dataclasses.field(default="", metadata=json_field(name="result", omitempty=True))
    Error: str = dataclasses.field(default="", metadata=json_field(name="error", omitempty=True))


def workspaceSpecPointer(spec: WorkspaceSpec) -> WorkspaceSpec:
    """A copy of ``spec``, so a caller cannot mutate what was persisted."""
    return WorkspaceSpec(PrimaryRoot=spec.PrimaryRoot, CWD=spec.CWD, Roots=tuple(spec.Roots))


class Repository(Protocol):
    """The outbound persistence port used by the session's use cases.

    Implementations decide how metadata, transcripts, and checkpoints are stored;
    the session layer only decides *when* each of them must be durable.
    """

    def Create(self, metadata: Metadata, initial: list[Message]) -> Metadata:
        """Create a new session, or raise."""
        ...

    def AssignNewTurnID(self, session_id: SessionID) -> None: ...

    def SaveMessage(self, session_id: SessionID, message: Message) -> None: ...

    def SaveApproval(self, session_id: SessionID, decision: ApprovalDecision, call: ToolCall | None) -> None: ...

    def SaveError(self, session_id: SessionID, error: BaseException) -> None: ...

    def SaveCancel(self, session_id: SessionID) -> None: ...

    def SaveReset(self, session_id: SessionID) -> None: ...

    def SaveConversationReplacement(self, session_id: SessionID, messages: list[Message]) -> None: ...

    def SaveCompaction(
        self, session_id: SessionID, summary: str, original: list[Message], kept: list[Message]
    ) -> None: ...

    def SaveCheckpoint(self, session_id: SessionID, call: ToolCall, files: list[FileSnapshot]) -> None: ...

    def List(self) -> list[Summary]: ...

    def Load(self, session_id: SessionID) -> tuple[list[Message], Metadata]: ...

    def LoadAuditEvents(self, session_id: SessionID) -> list[AuditEvent]: ...

    def RenameSession(self, session_id: SessionID, title: str) -> None: ...

    def Delete(self, session_id: SessionID) -> None: ...

    def LoadUndoPoint(self, session_id: SessionID) -> tuple[list[FileSnapshot], list[Message], int]:
        """The files of the most recent non-empty checkpoint, the transcript as of
        that checkpoint, and the record index for :meth:`TruncateAfter`."""
        ...

    def TruncateAfter(self, session_id: SessionID, index: int) -> None:
        """Drop every record after ``index``, keeping the checkpoint record itself."""
        ...

    def LoadMemory(self) -> list[str]: ...

    def SaveMemory(self, items: list[str]) -> None: ...

    def SaveWorkspaceDescription(self, session_id: SessionID, spec: WorkspaceSpec) -> None:
        """Persist the durable workspace description and its canonical cwd without
        touching unrelated metadata.

        Resume uses it to upgrade legacy metadata to canonical ``WorkspaceSpec``
        form exactly once.
        """
        ...


class Workspace(Protocol):
    """The outbound filesystem port used by checkpoints, attachments, and exports."""

    def Spec(self) -> WorkspaceSpec: ...

    def Validate(self, spec: WorkspaceSpec) -> None: ...

    def Canonicalize(self, spec: WorkspaceSpec) -> WorkspaceSpec:
        """Re-resolve every path in ``spec`` against the current filesystem.

        Resume uses it once to upgrade legacy metadata, whose saved cwd never
        promised a canonical path; new specs stay strict.
        """
        ...

    def Activate(self, spec: WorkspaceSpec) -> None: ...

    def Capture(self, paths: list[str]) -> list[FileSnapshot]: ...

    def Restore(self, files: list[FileSnapshot]) -> None: ...
