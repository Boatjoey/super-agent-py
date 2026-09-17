"""Facade re-exports for ``runtime/session``.

The session's compatibility surface, so callers can write
``runtime.new_session(...)``. R4 in
``tests/architecture/test_dependencies.py`` keeps this file from reaching any
concrete adapter.
"""

from __future__ import annotations

from super_agent.runtime.session import (
    APPROVE_ALWAYS as APPROVE_ALWAYS,
    APPROVE_ONCE as APPROVE_ONCE,
    DENY_APPROVAL as DENY_APPROVAL,
    WORKSPACE_ACCESS_READ as WORKSPACE_ACCESS_READ,
    WORKSPACE_ACCESS_READ_WRITE as WORKSPACE_ACCESS_READ_WRITE,
    ApprovalDecision as ApprovalDecision,
    AuditEvent as AuditEvent,
    EngineView as EngineView,
    FileSnapshot as FileSnapshot,
    MessageAppended as MessageAppended,
    Metadata as Metadata,
    Repository as Repository,
    Session as Session,
    SessionError as SessionError,
    SessionID as SessionID,
    SessionNotification as SessionNotification,
    StateChanged as StateChanged,
    StreamChunkReceived as StreamChunkReceived,
    Summary as Summary,
    ToolApprovalCleared as ToolApprovalCleared,
    ToolApprovalRequested as ToolApprovalRequested,
    UsageReported as UsageReported,
    Workspace as Workspace,
    WorkspaceAccessMode as WorkspaceAccessMode,
    WorkspaceRootSpec as WorkspaceRootSpec,
    WorkspaceSpec as WorkspaceSpec,
    create_persistent_session as create_persistent_session,
    new_persistent_session as new_persistent_session,
    new_session as new_session,
)

__all__ = [
    "APPROVE_ALWAYS",
    "APPROVE_ONCE",
    "DENY_APPROVAL",
    "WORKSPACE_ACCESS_READ",
    "WORKSPACE_ACCESS_READ_WRITE",
    "ApprovalDecision",
    "AuditEvent",
    "EngineView",
    "FileSnapshot",
    "MessageAppended",
    "Metadata",
    "Repository",
    "Session",
    "SessionError",
    "SessionID",
    "SessionNotification",
    "StateChanged",
    "StreamChunkReceived",
    "Summary",
    "ToolApprovalCleared",
    "ToolApprovalRequested",
    "UsageReported",
    "Workspace",
    "WorkspaceAccessMode",
    "WorkspaceRootSpec",
    "WorkspaceSpec",
    "create_persistent_session",
    "new_persistent_session",
    "new_session",
]
