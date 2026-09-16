"""Application use cases over the engine, and the ports they reach through.

The session owns no terminal behaviour and no scheduling: it supplies an approval
waiter, a streaming callback, a per-turn state observer, and the durability that
makes each of them survive a restart. R5 in
``tests/architecture/test_dependencies.py`` keeps this layer off ``os``,
``pathlib``, ``store``, and ``tui``.

A package spans several modules here, so this module stands in for the package
namespace.
"""

from __future__ import annotations

from super_agent.runtime.session.aliases import (
    ApprovalDecision as ApprovalDecision,
    ApprovalWaiter as ApprovalWaiter,
    ApproveAlways as ApproveAlways,
    ApproveOnce as ApproveOnce,
    Attachment as Attachment,
    DenyApproval as DenyApproval,
    Engine as Engine,
    EngineView as EngineView,
    Message as Message,
    PermissionMode as PermissionMode,
    PermissionModeAsk as PermissionModeAsk,
    PermissionModeBypass as PermissionModeBypass,
    PermissionRequest as PermissionRequest,
    PermissionRules as PermissionRules,
    RoleSystem as RoleSystem,
    RoleTool as RoleTool,
    State as State,
    StreamChunk as StreamChunk,
    ToolCall as ToolCall,
    UserMessageSubmitted as UserMessageSubmitted,
    ValidPermissionMode as ValidPermissionMode,
)
from super_agent.runtime.session.attachments import (
    AttachmentReader as AttachmentReader,
)
from super_agent.runtime.session.checkpoint import (
    CheckpointMixin as CheckpointMixin,
    checkpointPaths as checkpointPaths,
)
from super_agent.runtime.session.export import (
    ExportMixin as ExportMixin,
    ExportWriter as ExportWriter,
    htmlExport as htmlExport,
    markdownExport as markdownExport,
)
from super_agent.runtime.session.history import (
    MEMORY_SYSTEM_PREFIX as MEMORY_SYSTEM_PREFIX,
    HistoryMixin as HistoryMixin,
    compactedMessages as compactedMessages,
    nonSystemMessageCount as nonSystemMessageCount,
    savedWorkspaceSpec as savedWorkspaceSpec,
    withMemoryContext as withMemoryContext,
)
from super_agent.runtime.session.notifications import (
    NOTIFICATIONS_CLOSED as NOTIFICATIONS_CLOSED,
    MessageAppended as MessageAppended,
    NotificationsClosed as NotificationsClosed,
    SessionError as SessionError,
    SessionNotification as SessionNotification,
    StateChanged as StateChanged,
    StreamChunkReceived as StreamChunkReceived,
    ToolApprovalCleared as ToolApprovalCleared,
    ToolApprovalRequested as ToolApprovalRequested,
)
from super_agent.runtime.session.persistence import (
    PersistenceMixin as PersistenceMixin,
)
from super_agent.runtime.session.repository import (
    AuditEvent as AuditEvent,
    FileSnapshot as FileSnapshot,
    Metadata as Metadata,
    Repository as Repository,
    SessionID as SessionID,
    Summary as Summary,
    Workspace as Workspace,
    WorkspaceAccessMode as WorkspaceAccessMode,
    WorkspaceAccessRead as WorkspaceAccessRead,
    WorkspaceAccessReadWrite as WorkspaceAccessReadWrite,
    WorkspaceRootSpec as WorkspaceRootSpec,
    WorkspaceSpec as WorkspaceSpec,
    workspaceSpecPointer as workspaceSpecPointer,
)
from super_agent.runtime.session.session import (
    Closer as Closer,
    CreatePersistentSession as CreatePersistentSession,
    NewPersistentSession as NewPersistentSession,
    NewSession as NewSession,
    Session as Session,
    SessionBase as SessionBase,
)
from super_agent.runtime.session.snapshot_emitter import (
    SnapshotEmitter as SnapshotEmitter,
    permission_request as permission_request,
)
from super_agent.runtime.session.turn import (
    APPROVALS_CLOSED as APPROVALS_CLOSED,
    ApprovalsClosed as ApprovalsClosed,
    TurnMixin as TurnMixin,
    waitApproval as waitApproval,
)

__all__ = [
    "APPROVALS_CLOSED",
    "MEMORY_SYSTEM_PREFIX",
    "NOTIFICATIONS_CLOSED",
    "ApprovalDecision",
    "ApprovalWaiter",
    "ApprovalsClosed",
    "ApproveAlways",
    "ApproveOnce",
    "Attachment",
    "AttachmentReader",
    "AuditEvent",
    "CheckpointMixin",
    "Closer",
    "CreatePersistentSession",
    "DenyApproval",
    "Engine",
    "EngineView",
    "ExportMixin",
    "ExportWriter",
    "FileSnapshot",
    "HistoryMixin",
    "Message",
    "MessageAppended",
    "Metadata",
    "NewPersistentSession",
    "NewSession",
    "NotificationsClosed",
    "PermissionMode",
    "PermissionModeAsk",
    "PermissionModeBypass",
    "PermissionRequest",
    "PermissionRules",
    "PersistenceMixin",
    "Repository",
    "RoleSystem",
    "RoleTool",
    "Session",
    "SessionBase",
    "SessionError",
    "SessionID",
    "SessionNotification",
    "SnapshotEmitter",
    "State",
    "StateChanged",
    "StreamChunk",
    "StreamChunkReceived",
    "Summary",
    "ToolApprovalCleared",
    "ToolApprovalRequested",
    "ToolCall",
    "TurnMixin",
    "UserMessageSubmitted",
    "ValidPermissionMode",
    "Workspace",
    "WorkspaceAccessMode",
    "WorkspaceAccessRead",
    "WorkspaceAccessReadWrite",
    "WorkspaceRootSpec",
    "WorkspaceSpec",
    "checkpointPaths",
    "compactedMessages",
    "htmlExport",
    "markdownExport",
    "nonSystemMessageCount",
    "permission_request",
    "savedWorkspaceSpec",
    "waitApproval",
    "withMemoryContext",
    "workspaceSpecPointer",
]
