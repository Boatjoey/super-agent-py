"""The session :class:`~super_agent.runtime.session.Repository` over :class:`Store`.

This is where the store's own DTOs meet the use-case layer's ports: the store
keeps timestamps, the turn id, and the instruction fingerprint, while the session
only needs the fields it acts on, and the two metadata shapes are mapped field by
field so neither becomes an alias of the other.
"""

from __future__ import annotations

import sys

from super_agent import timeutil
from super_agent.runtime.session import (
    ROLE_TOOL,
    ApprovalDecision,
    AuditEvent,
    FileSnapshot,
    Message,
    Metadata,
    SessionID,
    Summary,
    ToolCall,
    WorkspaceAccessMode,
    WorkspaceRootSpec,
    WorkspaceSpec,
)
from super_agent.store.store import (
    EVENT_APPROVAL_DECISION,
    EVENT_CANCEL,
    EVENT_CHECKPOINT,
    EVENT_COMPACT,
    EVENT_CONTEXT_REPLACED,
    EVENT_ERROR,
    EVENT_MESSAGE_APPENDED,
    EVENT_RESET,
    EVENT_TOOL_RESULT,
    Checkpoint,
    Compact,
    FileSnapshot as StoreFileSnapshot,
    Metadata as StoreMetadata,
    Record,
    SessionID as StoreSessionID,
    Store,
    WorkspaceRootSpec as StoreWorkspaceRootSpec,
    WorkspaceSpec as StoreWorkspaceSpec,
    new_turn_id,
)


class Repository:
    """Durable session storage behind the session's persistence port."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def create(self, metadata: Metadata, initial: list[Message]) -> Metadata:
        """Persist a new session and return its stored metadata."""
        return toSessionMetadata(self.store.create(toStoreMetadata(metadata), initial))

    def assign_new_turn_id(self, session_id: SessionID) -> None:
        try:
            self.store.set_current_turn(StoreSessionID(session_id), new_turn_id(timeutil.now()))
        except Exception as error:
            logPersistenceFailure("start turn", session_id, error)
            raise

    def save_message(self, session_id: SessionID, message: Message) -> None:
        """A ``tool`` message is stored as a tool result, everything else as appended."""
        record = Record(type=EVENT_MESSAGE_APPENDED, message=message)
        if message.role == ROLE_TOOL:
            record = Record(
                type=EVENT_TOOL_RESULT,
                tool_call=ToolCall(id=message.tool_call_id, name=message.tool_name),
                result=message.content,
            )
        try:
            self.store.append(StoreSessionID(session_id), record)
        except Exception as error:
            logPersistenceFailure("save message", session_id, error)
            raise

    def save_approval(self, session_id: SessionID, decision: ApprovalDecision, call: ToolCall | None) -> None:
        record = Record(type=EVENT_APPROVAL_DECISION, decision=str(decision), tool_call=call)
        try:
            self.store.append(StoreSessionID(session_id), record)
        except Exception as error:
            logPersistenceFailure("save approval", session_id, error)
            raise

    def save_error(self, session_id: SessionID, error: BaseException | None) -> None:
        if error is None:
            return
        try:
            self.store.append(StoreSessionID(session_id), Record(type=EVENT_ERROR, error=str(error)))
        except Exception as append_error:
            logPersistenceFailure("save error", session_id, append_error)
            raise

    def save_cancel(self, session_id: SessionID) -> None:
        try:
            self.store.append(StoreSessionID(session_id), Record(type=EVENT_CANCEL))
        except Exception as error:
            logPersistenceFailure("save cancel", session_id, error)
            raise

    def save_reset(self, session_id: SessionID) -> None:
        try:
            self.store.append(StoreSessionID(session_id), Record(type=EVENT_RESET))
        except Exception as error:
            logPersistenceFailure("save reset", session_id, error)
            raise

    def save_conversation_replacement(self, session_id: SessionID, messages: list[Message]) -> None:
        record = Record(type=EVENT_CONTEXT_REPLACED, messages=tuple(messages))
        try:
            self.store.append(StoreSessionID(session_id), record)
        except Exception as error:
            logPersistenceFailure("replace conversation", session_id, error)
            raise

    def save_compaction(
        self, session_id: SessionID, summary: str, original: list[Message], kept: list[Message]
    ) -> None:
        compact = Compact(summary=summary, original_messages=tuple(original), kept_messages=tuple(kept))
        try:
            self.store.append(StoreSessionID(session_id), Record(type=EVENT_COMPACT, compact=compact))
        except Exception as error:
            logPersistenceFailure("save compaction", session_id, error)
            raise

    def save_checkpoint(self, session_id: SessionID, call: ToolCall, files: list[FileSnapshot]) -> None:
        """Record the files a call is about to change; an empty capture is not a checkpoint."""
        if not files:
            return
        checkpoint = Checkpoint(
            id=str(new_turn_id(timeutil.now())),
            files=tuple(
                StoreFileSnapshot(path=file.path, exists=file.exists, content=file.content, mode=file.mode)
                for file in files
            ),
            reason=call.name,
        )
        record = Record(type=EVENT_CHECKPOINT, tool_call=call, checkpoint=checkpoint)
        try:
            self.store.append(StoreSessionID(session_id), record)
        except Exception as error:
            logPersistenceFailure("save checkpoint", session_id, error)
            raise

    def list(self) -> list[Summary]:
        return [
            Summary(
                id=SessionID(item.id),
                title=item.title,
                updated_at=item.updated_at,
                provider=item.provider,
                model=item.model,
                cwd=item.cwd,
                parent_id=SessionID(item.parent_id),
            )
            for item in self.store.list()
        ]

    def load(self, session_id: SessionID) -> tuple[list[Message], Metadata]:
        messages = self.store.messages(StoreSessionID(session_id))
        meta = self.store.metadata(StoreSessionID(session_id))
        return messages, toSessionMetadata(meta)

    def load_audit_events(self, session_id: SessionID) -> list[AuditEvent]:
        """Approvals, tool results, errors, and cancels; never sent to a model."""
        result: list[AuditEvent] = []
        for record in self.store.records(StoreSessionID(session_id)):
            if record.type in (EVENT_APPROVAL_DECISION, EVENT_TOOL_RESULT, EVENT_ERROR, EVENT_CANCEL):
                result.append(
                    AuditEvent(
                        type=record.type,
                        time=record.time,
                        tool_call=record.tool_call,
                        decision=record.decision,
                        result=record.result,
                        error=record.error,
                    )
                )
        return result

    def rename_session(self, session_id: SessionID, title: str) -> None:
        self.store.rename_session(StoreSessionID(session_id), title)

    def delete(self, session_id: SessionID) -> None:
        self.store.delete(StoreSessionID(session_id))

    def load_undo_point(self, session_id: SessionID) -> tuple[list[FileSnapshot], list[Message], int]:
        """The files of the newest non-empty checkpoint, the transcript as of it, and its index."""
        checkpoint, messages, index = self.store.checkpoint_undo(StoreSessionID(session_id))
        files = [
            FileSnapshot(path=file.path, exists=file.exists, content=file.content, mode=file.mode)
            for file in checkpoint.files
        ]
        return files, messages, index

    def truncate_after(self, session_id: SessionID, index: int) -> None:
        self.store.truncate_after(StoreSessionID(session_id), index)

    def load_memory(self) -> list[str]:
        return self.store.load_memory()

    def save_memory(self, items: list[str]) -> None:
        self.store.save_memory(items)

    def save_workspace_description(self, session_id: SessionID, spec: WorkspaceSpec) -> None:
        stored = toStoreWorkspaceSpec(spec)
        assert stored is not None
        self.store.save_workspace_description(StoreSessionID(session_id), stored)


def new_repository(store: Store) -> Repository:
    """The repository over ``store``."""
    return Repository(store)


def toSessionMetadata(meta: StoreMetadata) -> Metadata:
    """The session view of a stored metadata record."""
    return Metadata(
        id=SessionID(meta.id),
        title=meta.title,
        provider=meta.provider,
        model=meta.model,
        cwd=meta.cwd,
        instruction_sources=tuple(meta.instruction_sources),
        parent_id=SessionID(meta.parent_id),
        project_id=meta.project_id,
        config_root=meta.config_root,
        workspace_spec=toSessionWorkspaceSpec(meta.workspace),
    )


def toStoreMetadata(meta: Metadata) -> StoreMetadata:
    """The stored view of a session metadata record."""
    return StoreMetadata(
        id=StoreSessionID(meta.id),
        title=meta.title,
        provider=meta.provider,
        model=meta.model,
        cwd=meta.cwd,
        instruction_sources=tuple(meta.instruction_sources),
        parent_id=StoreSessionID(meta.parent_id),
        project_id=meta.project_id,
        config_root=meta.config_root,
        workspace=toStoreWorkspaceSpec(meta.workspace_spec),
    )


def toStoreWorkspaceSpec(spec: WorkspaceSpec | None) -> StoreWorkspaceSpec | None:
    if spec is None:
        return None
    roots = tuple(StoreWorkspaceRootSpec(path=root.path, access=str(root.access)) for root in spec.roots)
    return StoreWorkspaceSpec(primary_root=spec.primary_root, cwd=spec.cwd, roots=roots)


def toSessionWorkspaceSpec(spec: StoreWorkspaceSpec | None) -> WorkspaceSpec | None:
    if spec is None:
        return None
    roots = tuple(WorkspaceRootSpec(path=root.path, access=WorkspaceAccessMode(root.access)) for root in spec.roots)
    return WorkspaceSpec(primary_root=spec.primary_root, cwd=spec.cwd, roots=roots)


def logPersistenceFailure(operation: str, session_id: SessionID, error: BaseException) -> None:
    """Keep best-effort persistence visible: the turn keeps running, but transcript loss is reported."""
    print(f"super-agent: failed to persist {operation} for session {session_id}: {error}", file=sys.stderr)
