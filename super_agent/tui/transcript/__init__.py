"""The transcript feature.

Go's ``tui/transcript`` package surface, re-exported so callers keep writing
``transcript.Model`` and ``transcript.RoleAssistant``.
"""

from __future__ import annotations

from super_agent.tui.transcript.model import (
    Attachment as Attachment,
    DefaultStyles as DefaultStyles,
    ExtractCodeBlocks as ExtractCodeBlocks,
    Intent as Intent,
    MarkdownRenderer as MarkdownRenderer,
    Message as Message,
    Model as Model,
    New as New,
    Role as Role,
    RoleAssistant as RoleAssistant,
    RoleUser as RoleUser,
    Styles as Styles,
    ToolCall as ToolCall,
    ToolDisplayGroup as ToolDisplayGroup,
    toolDisplay as toolDisplay,
    toolGroups as toolGroups,
)

__all__ = [
    "Attachment",
    "DefaultStyles",
    "ExtractCodeBlocks",
    "Intent",
    "MarkdownRenderer",
    "Message",
    "Model",
    "New",
    "Role",
    "RoleAssistant",
    "RoleUser",
    "Styles",
    "ToolCall",
    "ToolDisplayGroup",
    "toolDisplay",
    "toolGroups",
]
