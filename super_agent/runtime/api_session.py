"""Facade re-exports for ``runtime/session``.

Go's ``runtime/api_session.go`` keeps the session's compatibility surface in one
file so callers can write ``runtime.NewSession(...)``. R4 in
``tests/architecture/test_dependencies.py`` keeps this file from reaching any
concrete adapter.
"""

from __future__ import annotations

from super_agent.runtime.session import (
    ApprovalDecision as ApprovalDecision,
    ApproveAlways as ApproveAlways,
    ApproveOnce as ApproveOnce,
    AuditEvent as AuditEvent,
    CreatePersistentSession as CreatePersistentSession,
    DenyApproval as DenyApproval,
    EngineView as EngineView,
    FileSnapshot as FileSnapshot,
    MessageAppended as MessageAppended,
    Metadata as Metadata,
    NewPersistentSession as NewPersistentSession,
    NewSession as NewSession,
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
    Workspace as Workspace,
    WorkspaceAccessMode as WorkspaceAccessMode,
    WorkspaceAccessRead as WorkspaceAccessRead,
    WorkspaceAccessReadWrite as WorkspaceAccessReadWrite,
    WorkspaceRootSpec as WorkspaceRootSpec,
    WorkspaceSpec as WorkspaceSpec,
)

__all__ = [
    "ApprovalDecision",
    "ApproveAlways",
    "ApproveOnce",
    "AuditEvent",
    "CreatePersistentSession",
    "DenyApproval",
    "EngineView",
    "FileSnapshot",
    "MessageAppended",
    "Metadata",
    "NewPersistentSession",
    "NewSession",
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
    "Workspace",
    "WorkspaceAccessMode",
    "WorkspaceAccessRead",
    "WorkspaceAccessReadWrite",
    "WorkspaceRootSpec",
    "WorkspaceSpec",
]
