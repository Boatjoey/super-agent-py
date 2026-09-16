"""Location-stable re-exports of the protocol and permission values.

Go's ``machine/aliases.go`` exists so other packages can write
``machine.Message`` without importing ``runtime/protocol`` themselves. The same
names are re-exported here at the same position, so ``machine.Message`` and
``machine.PermissionRequest`` keep working.
"""

from __future__ import annotations

from super_agent.runtime.permission.types import (
    CommandClass as CommandClass,
    CommandClassDestructive as CommandClassDestructive,
    CommandClassNetwork as CommandClassNetwork,
    CommandClassReadOnly as CommandClassReadOnly,
    CommandClassUnknown as CommandClassUnknown,
    CommandClassWrite as CommandClassWrite,
    Request as PermissionRequest,
    ZeroCommandClass as ZeroCommandClass,
)
from super_agent.runtime.protocol.types import (
    Attachment as Attachment,
    Message as Message,
    ModelResponse as ModelResponse,
    Role as Role,
    RoleAssistant as RoleAssistant,
    RoleSystem as RoleSystem,
    RoleTool as RoleTool,
    RoleUser as RoleUser,
    StreamChunk as StreamChunk,
    ToolCall as ToolCall,
)

__all__ = [
    "Attachment",
    "CommandClass",
    "CommandClassDestructive",
    "CommandClassNetwork",
    "CommandClassReadOnly",
    "CommandClassUnknown",
    "CommandClassWrite",
    "Message",
    "ModelResponse",
    "PermissionRequest",
    "Role",
    "RoleAssistant",
    "RoleSystem",
    "RoleTool",
    "RoleUser",
    "StreamChunk",
    "ToolCall",
    "ZeroCommandClass",
]
