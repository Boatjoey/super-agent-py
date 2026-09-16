"""The slash-command feature.

Go's ``tui/commands`` package surface, re-exported so callers keep writing
``commands.Outcome`` and ``commands.IsCommand``. ``Command`` stays the palette
entry Go makes it; the effect type a handler returns stays inside
``commands/model.py``.
"""

from __future__ import annotations

from super_agent.tui.commands.catalog import (
    Command as Command,
    IsCommand as IsCommand,
)
from super_agent.tui.commands.format import (
    divider as divider,
    formatInstructions as formatInstructions,
    formatMCPServers as formatMCPServers,
    formatNamedItems as formatNamedItems,
    formatPermissions as formatPermissions,
    formatSessions as formatSessions,
)
from super_agent.tui.commands.model import (
    Config as Config,
    Input as Input,
    Model as Model,
    New as New,
)
from super_agent.tui.commands.outcome import (
    CompactDone as CompactDone,
    MCPDone as MCPDone,
    Outcome as Outcome,
    StatusBar as StatusBar,
)
from super_agent.tui.commands.ports import (
    AgentPort as AgentPort,
    AgentSummary as AgentSummary,
    Attachment as Attachment,
    ExtensionPort as ExtensionPort,
    MCPPort as MCPPort,
    MCPServerSummary as MCPServerSummary,
    MemoryPort as MemoryPort,
    PermissionPort as PermissionPort,
    Ports as Ports,
    SessionPort as SessionPort,
    SessionSummary as SessionSummary,
    WorkspacePort as WorkspacePort,
)

__all__ = [
    "AgentPort",
    "AgentSummary",
    "Attachment",
    "Command",
    "CompactDone",
    "Config",
    "ExtensionPort",
    "Input",
    "IsCommand",
    "MCPDone",
    "MCPPort",
    "MCPServerSummary",
    "MemoryPort",
    "Model",
    "New",
    "Outcome",
    "PermissionPort",
    "Ports",
    "SessionPort",
    "SessionSummary",
    "StatusBar",
    "WorkspacePort",
    "divider",
    "formatInstructions",
    "formatMCPServers",
    "formatNamedItems",
    "formatPermissions",
    "formatSessions",
]
