"""Facade re-exports for the value types the adapters share.

These are grouped in one module so the whole protocol surface is reachable as
``runtime.Message`` and ``runtime.ToolSpec``.
"""

from __future__ import annotations

from super_agent.runtime.machine import (
    ROLE_ASSISTANT as ROLE_ASSISTANT,
    ROLE_SYSTEM as ROLE_SYSTEM,
    ROLE_TOOL as ROLE_TOOL,
    ROLE_USER as ROLE_USER,
    STATE_ADVANCING_QUEUE as STATE_ADVANCING_QUEUE,
    STATE_IDLE as STATE_IDLE,
    STATE_INITIALIZING as STATE_INITIALIZING,
    STATE_RUNNING_TOOL as STATE_RUNNING_TOOL,
    STATE_WAITING_APPROVAL as STATE_WAITING_APPROVAL,
    STATE_WAITING_LLM as STATE_WAITING_LLM,
    Attachment as Attachment,
    Message as Message,
    ModelResponse as ModelResponse,
    Role as Role,
    State as State,
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
    Usage as Usage,
)

__all__ = [
    "ROLE_ASSISTANT",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ROLE_USER",
    "STATE_ADVANCING_QUEUE",
    "STATE_IDLE",
    "STATE_INITIALIZING",
    "STATE_RUNNING_TOOL",
    "STATE_WAITING_APPROVAL",
    "STATE_WAITING_LLM",
    "Attachment",
    "CommandClass",
    "Message",
    "Model",
    "ModelResponse",
    "PermissionRequest",
    "Role",
    "State",
    "StreamChunk",
    "ToolCall",
    "ToolCallBatch",
    "ToolRunner",
    "ToolSpec",
    "Usage",
]
