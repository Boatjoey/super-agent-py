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
    APPROVE_ALWAYS as APPROVE_ALWAYS,
    APPROVE_ONCE as APPROVE_ONCE,
    DENY_APPROVAL as DENY_APPROVAL,
    PERMISSION_MODE_ASK as PERMISSION_MODE_ASK,
    PERMISSION_MODE_BYPASS as PERMISSION_MODE_BYPASS,
    ROLE_SYSTEM as ROLE_SYSTEM,
    ROLE_TOOL as ROLE_TOOL,
    ApprovalDecision as ApprovalDecision,
    ApprovalWaiter as ApprovalWaiter,
    Attachment as Attachment,
    Engine as Engine,
    EngineView as EngineView,
    Message as Message,
    PermissionMode as PermissionMode,
    PermissionRequest as PermissionRequest,
    PermissionRules as PermissionRules,
    State as State,
    StreamChunk as StreamChunk,
    ToolCall as ToolCall,
    UserMessageSubmitted as UserMessageSubmitted,
    valid_permission_mode as valid_permission_mode,
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
    UsageReported as UsageReported,
)
from super_agent.runtime.session.persistence import (
    PersistenceMixin as PersistenceMixin,
)
from super_agent.runtime.session.repository import (
    WORKSPACE_ACCESS_READ as WORKSPACE_ACCESS_READ,
    WORKSPACE_ACCESS_READ_WRITE as WORKSPACE_ACCESS_READ_WRITE,
    AuditEvent as AuditEvent,
    FileSnapshot as FileSnapshot,
    Metadata as Metadata,
    Repository as Repository,
    SessionID as SessionID,
    Summary as Summary,
    Workspace as Workspace,
    WorkspaceAccessMode as WorkspaceAccessMode,
    WorkspaceRootSpec as WorkspaceRootSpec,
    WorkspaceSpec as WorkspaceSpec,
    workspaceSpecPointer as workspaceSpecPointer,
)
from super_agent.runtime.session.session import (
    Closer as Closer,
    Session as Session,
    SessionBase as SessionBase,
    create_persistent_session as create_persistent_session,
    new_persistent_session as new_persistent_session,
    new_session as new_session,
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
    "APPROVE_ALWAYS",
    "APPROVE_ONCE",
    "DENY_APPROVAL",
    "MEMORY_SYSTEM_PREFIX",
    "NOTIFICATIONS_CLOSED",
    "PERMISSION_MODE_ASK",
    "PERMISSION_MODE_BYPASS",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "WORKSPACE_ACCESS_READ",
    "WORKSPACE_ACCESS_READ_WRITE",
    "ApprovalDecision",
    "ApprovalWaiter",
    "ApprovalsClosed",
    "Attachment",
    "AttachmentReader",
    "AuditEvent",
    "CheckpointMixin",
    "Closer",
    "Engine",
    "EngineView",
    "ExportMixin",
    "ExportWriter",
    "FileSnapshot",
    "HistoryMixin",
    "Message",
    "MessageAppended",
    "Metadata",
    "NotificationsClosed",
    "PermissionMode",
    "PermissionRequest",
    "PermissionRules",
    "PersistenceMixin",
    "Repository",
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
    "UsageReported",
    "UserMessageSubmitted",
    "Workspace",
    "WorkspaceAccessMode",
    "WorkspaceRootSpec",
    "WorkspaceSpec",
    "checkpointPaths",
    "compactedMessages",
    "create_persistent_session",
    "htmlExport",
    "markdownExport",
    "new_persistent_session",
    "new_session",
    "nonSystemMessageCount",
    "permission_request",
    "savedWorkspaceSpec",
    "valid_permission_mode",
    "waitApproval",
    "withMemoryContext",
    "workspaceSpecPointer",
]
