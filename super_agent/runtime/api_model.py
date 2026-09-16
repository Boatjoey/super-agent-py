"""Facade re-exports for the value types the adapters share.

Go's ``runtime/api_model.go`` keeps these in one file so the whole protocol
surface is reachable as ``runtime.Message`` and ``runtime.ToolSpec``.
"""

from __future__ import annotations

from super_agent.runtime.machine import (
    Attachment as Attachment,
    Message as Message,
    ModelResponse as ModelResponse,
    Role as Role,
    RoleAssistant as RoleAssistant,
    RoleSystem as RoleSystem,
    RoleTool as RoleTool,
    RoleUser as RoleUser,
    State as State,
    StateAdvancingQueue as StateAdvancingQueue,
    StateIdle as StateIdle,
    StateInitializing as StateInitializing,
    StateRunningTool as StateRunningTool,
    StateWaitingApproval as StateWaitingApproval,
    StateWaitingLLM as StateWaitingLLM,
    StreamChunk as StreamChunk,
    ToolCall as ToolCall,
    ToolCallBatch as ToolCallBatch,
)
from super_agent.runtime.permission.types import (
    CommandClass as CommandClass,
    Request as PermissionRequest,
)
from super_agent.runtime.protocol.types import (
    Model as Model,
    ToolRunner as ToolRunner,
    ToolSpec as ToolSpec,
)

__all__ = [
    "Attachment",
    "CommandClass",
    "Message",
    "Model",
    "ModelResponse",
    "PermissionRequest",
    "Role",
    "RoleAssistant",
    "RoleSystem",
    "RoleTool",
    "RoleUser",
    "State",
    "StateAdvancingQueue",
    "StateIdle",
    "StateInitializing",
    "StateRunningTool",
    "StateWaitingApproval",
    "StateWaitingLLM",
    "StreamChunk",
    "ToolCall",
    "ToolCallBatch",
    "ToolRunner",
    "ToolSpec",
]
