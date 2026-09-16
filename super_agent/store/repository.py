"""The session :class:`~super_agent.runtime.session.Repository` over :class:`Store`.

This is where the store's own DTOs meet the use-case layer's ports: the store
keeps timestamps, the turn id, and the instruction fingerprint, while the session
only needs the fields it acts on, and the two metadata shapes are mapped field by
field so neither becomes an alias of the other.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from super_agent.runtime.session import (
    ApprovalDecision,
    AuditEvent,
    FileSnapshot,
    Message,
    Metadata,
    RoleTool,
    SessionID,
    Summary,
    ToolCall,
    WorkspaceAccessMode,
    WorkspaceRootSpec,
    WorkspaceSpec,
)
from super_agent.store.store import (
    Checkpoint,
    Compact,
    EventApprovalDecision,
    EventCancel,
    EventCheckpoint,
    EventCompact,
    EventContextReplaced,
    EventError,
    EventMessageAppended,
    EventReset,
    EventToolResult,
    FileSnapshot as StoreFileSnapshot,
    Metadata as StoreMetadata,
    NewTurnID,
    Record,
    SessionID as StoreSessionID,
    Store,
    WorkspaceRootSpec as StoreWorkspaceRootSpec,
    WorkspaceSpec as StoreWorkspaceSpec,
)


class Repository:
    """Durable session storage behind the session's persistence port."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def Create(self, metadata: Metadata, initial: list[Message]) -> Metadata:
        """Persist a new session and return its stored metadata."""
        return toSessionMetadata(self.store.Create(toStoreMetadata(metadata), initial))

    def AssignNewTurnID(self, session_id: SessionID) -> None:
        try:
            self.store.SetCurrentTurn(StoreSessionID(session_id), NewTurnID(datetime.now(UTC)))
        except Exception as error:
            logPersistenceFailure("start turn", session_id, error)
            raise

    def SaveMessage(self, session_id: SessionID, message: Message) -> None:
        """A ``tool`` message is stored as a tool result, everything else as appended."""
        record = Record(Type=EventMessageAppended, Message=message)
        if message.Role == RoleTool:
            record = Record(
                Type=EventToolResult,
                ToolCall=ToolCall(ID=message.ToolCallID, Name=message.ToolName),
                Result=message.Content,
            )
        try:
            self.store.Append(StoreSessionID(session_id), record)
        except Exception as error:
            logPersistenceFailure("save message", session_id, error)
            raise

    def SaveApproval(self, session_id: SessionID, decision: ApprovalDecision, call: ToolCall | None) -> None:
        record = Record(Type=EventApprovalDecision, Decision=str(decision), ToolCall=call)
        try:
            self.store.Append(StoreSessionID(session_id), record)
        except Exception as error:
            logPersistenceFailure("save approval", session_id, error)
            raise

    def SaveError(self, session_id: SessionID, error: BaseException | None) -> None:
        if error is None:
            return
        try:
            self.store.Append(StoreSessionID(session_id), Record(Type=EventError, Error=str(error)))
        except Exception as append_error:
            logPersistenceFailure("save error", session_id, append_error)
            raise

    def SaveCancel(self, session_id: SessionID) -> None:
        try:
            self.store.Append(StoreSessionID(session_id), Record(Type=EventCancel))
        except Exception as error:
            logPersistenceFailure("save cancel", session_id, error)
            raise

    def SaveReset(self, session_id: SessionID) -> None:
        try:
            self.store.Append(StoreSessionID(session_id), Record(Type=EventReset))
        except Exception as error:
            logPersistenceFailure("save reset", session_id, error)
            raise

    def SaveConversationReplacement(self, session_id: SessionID, messages: list[Message]) -> None:
        record = Record(Type=EventContextReplaced, Messages=tuple(messages))
        try:
            self.store.Append(StoreSessionID(session_id), record)
        except Exception as error:
            logPersistenceFailure("replace conversation", session_id, error)
            raise

    def SaveCompaction(self, session_id: SessionID, summary: str, original: list[Message], kept: list[Message]) -> None:
        compact = Compact(Summary=summary, OriginalMessages=tuple(original), KeptMessages=tuple(kept))
        try:
            self.store.Append(StoreSessionID(session_id), Record(Type=EventCompact, Compact=compact))
        except Exception as error:
            logPersistenceFailure("save compaction", session_id, error)
            raise

    def SaveCheckpoint(self, session_id: SessionID, call: ToolCall, files: list[FileSnapshot]) -> None:
        """Record the files a call is about to change; an empty capture is not a checkpoint."""
        if not files:
            return
        checkpoint = Checkpoint(
            ID=str(NewTurnID(datetime.now(UTC))),
            Files=tuple(
                StoreFileSnapshot(Path=file.Path, Exists=file.Exists, Content=file.Content, Mode=file.Mode)
                for file in files
            ),
            Reason=call.Name,
        )
        record = Record(Type=EventCheckpoint, ToolCall=call, Checkpoint=checkpoint)
        try:
            self.store.Append(StoreSessionID(session_id), record)
        except Exception as error:
            logPersistenceFailure("save checkpoint", session_id, error)
            raise

    def List(self) -> list[Summary]:
        return [
            Summary(
                ID=SessionID(item.ID),
                Title=item.Title,
                UpdatedAt=item.UpdatedAt,
                Provider=item.Provider,
                Model=item.Model,
                CWD=item.CWD,
                ParentID=SessionID(item.ParentID),
            )
            for item in self.store.List()
        ]

    def Load(self, session_id: SessionID) -> tuple[list[Message], Metadata]:
        messages = self.store.Messages(StoreSessionID(session_id))
        meta = self.store.Metadata(StoreSessionID(session_id))
        return messages, toSessionMetadata(meta)

    def LoadAuditEvents(self, session_id: SessionID) -> list[AuditEvent]:
        """Approvals, tool results, errors, and cancels; never sent to a model."""
        result: list[AuditEvent] = []
        for record in self.store.Records(StoreSessionID(session_id)):
            if record.Type in (EventApprovalDecision, EventToolResult, EventError, EventCancel):
                result.append(
                    AuditEvent(
                        Type=record.Type,
                        Time=record.Time,
                        ToolCall=record.ToolCall,
                        Decision=record.Decision,
                        Result=record.Result,
                        Error=record.Error,
                    )
                )
        return result

    def RenameSession(self, session_id: SessionID, title: str) -> None:
        self.store.RenameSession(StoreSessionID(session_id), title)

    def Delete(self, session_id: SessionID) -> None:
        self.store.Delete(StoreSessionID(session_id))

    def LoadUndoPoint(self, session_id: SessionID) -> tuple[list[FileSnapshot], list[Message], int]:
        """The files of the newest non-empty checkpoint, the transcript as of it, and its index."""
        checkpoint, messages, index = self.store.CheckpointUndo(StoreSessionID(session_id))
        files = [
            FileSnapshot(Path=file.Path, Exists=file.Exists, Content=file.Content, Mode=file.Mode)
            for file in checkpoint.Files
        ]
        return files, messages, index

    def TruncateAfter(self, session_id: SessionID, index: int) -> None:
        self.store.TruncateAfter(StoreSessionID(session_id), index)

    def LoadMemory(self) -> list[str]:
        return self.store.LoadMemory()

    def SaveMemory(self, items: list[str]) -> None:
        self.store.SaveMemory(items)

    def SaveWorkspaceDescription(self, session_id: SessionID, spec: WorkspaceSpec) -> None:
        stored = toStoreWorkspaceSpec(spec)
        assert stored is not None
        self.store.SaveWorkspaceDescription(StoreSessionID(session_id), stored)


def NewRepository(store: Store) -> Repository:
    """The repository over ``store``."""
    return Repository(store)


def toSessionMetadata(meta: StoreMetadata) -> Metadata:
    """The session view of a stored metadata record."""
    return Metadata(
        ID=SessionID(meta.ID),
        Title=meta.Title,
        Provider=meta.Provider,
        Model=meta.Model,
        CWD=meta.CWD,
        InstructionSources=tuple(meta.InstructionSources),
        ParentID=SessionID(meta.ParentID),
        ProjectID=meta.ProjectID,
        ConfigRoot=meta.ConfigRoot,
        WorkspaceSpec=toSessionWorkspaceSpec(meta.Workspace),
    )


def toStoreMetadata(meta: Metadata) -> StoreMetadata:
    """The stored view of a session metadata record."""
    return StoreMetadata(
        ID=StoreSessionID(meta.ID),
        Title=meta.Title,
        Provider=meta.Provider,
        Model=meta.Model,
        CWD=meta.CWD,
        InstructionSources=tuple(meta.InstructionSources),
        ParentID=StoreSessionID(meta.ParentID),
        ProjectID=meta.ProjectID,
        ConfigRoot=meta.ConfigRoot,
        Workspace=toStoreWorkspaceSpec(meta.WorkspaceSpec),
    )


def toStoreWorkspaceSpec(spec: WorkspaceSpec | None) -> StoreWorkspaceSpec | None:
    if spec is None:
        return None
    roots = tuple(StoreWorkspaceRootSpec(Path=root.Path, Access=str(root.Access)) for root in spec.Roots)
    return StoreWorkspaceSpec(PrimaryRoot=spec.PrimaryRoot, CWD=spec.CWD, Roots=roots)


def toSessionWorkspaceSpec(spec: StoreWorkspaceSpec | None) -> WorkspaceSpec | None:
    if spec is None:
        return None
    roots = tuple(WorkspaceRootSpec(Path=root.Path, Access=WorkspaceAccessMode(root.Access)) for root in spec.Roots)
    return WorkspaceSpec(PrimaryRoot=spec.PrimaryRoot, CWD=spec.CWD, Roots=roots)


def logPersistenceFailure(operation: str, session_id: SessionID, error: BaseException) -> None:
    """Keep best-effort persistence visible: the turn keeps running, but transcript loss is reported."""
    print(f"super-agent: failed to persist {operation} for session {session_id}: {error}", file=sys.stderr)
