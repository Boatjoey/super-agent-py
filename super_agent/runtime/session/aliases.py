"""Location-stable re-exports for the session package.

These re-exports let session code write ``Message`` and ``PermissionMode``
without importing half the runtime. The same names live at the same position
here.
"""

from __future__ import annotations

from super_agent.runtime.engine import Engine as Engine, EngineView as EngineView
from super_agent.runtime.execution import (
    ApprovalWaiter as ApprovalWaiter,
    PermissionMode as PermissionMode,
    PermissionModeAsk as PermissionModeAsk,
    PermissionModeBypass as PermissionModeBypass,
    PermissionRules as PermissionRules,
    ValidPermissionMode as ValidPermissionMode,
)
from super_agent.runtime.machine import (
    ApprovalDecision as ApprovalDecision,
    ApproveAlways as ApproveAlways,
    ApproveOnce as ApproveOnce,
    DenyApproval as DenyApproval,
    Message as Message,
    PermissionRequest as PermissionRequest,
    RoleSystem as RoleSystem,
    RoleTool as RoleTool,
    State as State,
    ToolCall as ToolCall,
    UserMessageSubmitted as UserMessageSubmitted,
)
from super_agent.runtime.protocol.types import (
    Attachment as Attachment,
    StreamChunk as StreamChunk,
)

__all__ = [
    "ApprovalDecision",
    "ApprovalWaiter",
    "ApproveAlways",
    "ApproveOnce",
    "Attachment",
    "DenyApproval",
    "Engine",
    "EngineView",
    "Message",
    "PermissionMode",
    "PermissionModeAsk",
    "PermissionModeBypass",
    "PermissionRequest",
    "PermissionRules",
    "RoleSystem",
    "RoleTool",
    "State",
    "StreamChunk",
    "ToolCall",
    "UserMessageSubmitted",
    "ValidPermissionMode",
]
