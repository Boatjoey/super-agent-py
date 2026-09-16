"""Location-stable re-exports for the session package.

These re-exports let session code write ``Message`` and ``PermissionMode``
without importing half the runtime. The same names live at the same position
here.
"""

from __future__ import annotations

from super_agent.runtime.engine import Engine as Engine, EngineView as EngineView
from super_agent.runtime.execution import (
    PERMISSION_MODE_ASK as PERMISSION_MODE_ASK,
    PERMISSION_MODE_BYPASS as PERMISSION_MODE_BYPASS,
    ApprovalWaiter as ApprovalWaiter,
    PermissionMode as PermissionMode,
    PermissionRules as PermissionRules,
    valid_permission_mode as valid_permission_mode,
)
from super_agent.runtime.machine import (
    APPROVE_ALWAYS as APPROVE_ALWAYS,
    APPROVE_ONCE as APPROVE_ONCE,
    DENY_APPROVAL as DENY_APPROVAL,
    ROLE_SYSTEM as ROLE_SYSTEM,
    ROLE_TOOL as ROLE_TOOL,
    ApprovalDecision as ApprovalDecision,
    Message as Message,
    PermissionRequest as PermissionRequest,
    State as State,
    ToolCall as ToolCall,
    UserMessageSubmitted as UserMessageSubmitted,
)
from super_agent.runtime.protocol.types import (
    Attachment as Attachment,
    StreamChunk as StreamChunk,
)

__all__ = [
    "APPROVE_ALWAYS",
    "APPROVE_ONCE",
    "DENY_APPROVAL",
    "PERMISSION_MODE_ASK",
    "PERMISSION_MODE_BYPASS",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ApprovalDecision",
    "ApprovalWaiter",
    "Attachment",
    "Engine",
    "EngineView",
    "Message",
    "PermissionMode",
    "PermissionRequest",
    "PermissionRules",
    "State",
    "StreamChunk",
    "ToolCall",
    "UserMessageSubmitted",
    "valid_permission_mode",
]
