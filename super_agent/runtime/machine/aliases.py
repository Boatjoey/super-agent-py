"""Location-stable re-exports of the protocol and permission values.

These re-exports let other packages write ``machine.Message`` without importing
``runtime/protocol`` themselves. The same names are re-exported here at the same
position, so ``machine.Message`` and ``machine.PermissionRequest`` keep working.
"""

from __future__ import annotations

from super_agent.runtime.permission.types import (
    COMMAND_CLASS_DESTRUCTIVE as COMMAND_CLASS_DESTRUCTIVE,
    COMMAND_CLASS_NETWORK as COMMAND_CLASS_NETWORK,
    COMMAND_CLASS_READ_ONLY as COMMAND_CLASS_READ_ONLY,
    COMMAND_CLASS_UNKNOWN as COMMAND_CLASS_UNKNOWN,
    COMMAND_CLASS_WRITE as COMMAND_CLASS_WRITE,
    ZERO_COMMAND_CLASS as ZERO_COMMAND_CLASS,
    CommandClass as CommandClass,
    Request as PermissionRequest,
)
from super_agent.runtime.protocol.types import (
    ROLE_ASSISTANT as ROLE_ASSISTANT,
    ROLE_SYSTEM as ROLE_SYSTEM,
    ROLE_TOOL as ROLE_TOOL,
    ROLE_USER as ROLE_USER,
    Attachment as Attachment,
    Message as Message,
    ModelResponse as ModelResponse,
    Role as Role,
    StreamChunk as StreamChunk,
    ToolCall as ToolCall,
)

__all__ = [
    "COMMAND_CLASS_DESTRUCTIVE",
    "COMMAND_CLASS_NETWORK",
    "COMMAND_CLASS_READ_ONLY",
    "COMMAND_CLASS_UNKNOWN",
    "COMMAND_CLASS_WRITE",
    "ROLE_ASSISTANT",
    "ROLE_SYSTEM",
    "ROLE_TOOL",
    "ROLE_USER",
    "ZERO_COMMAND_CLASS",
    "Attachment",
    "CommandClass",
    "Message",
    "ModelResponse",
    "PermissionRequest",
    "Role",
    "StreamChunk",
    "ToolCall",
]
