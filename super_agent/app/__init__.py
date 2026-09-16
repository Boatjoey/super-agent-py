"""The composition root's public surface.

The app package surface: configuration and settings, the session and agent
constructors, the MCP and workflow controllers, and the terminal adapter. Callers
write ``app.LoadConfig``; the re-exports here keep that spelling.

``app`` is the only place the concrete adapters meet. It may import ``llm``,
``tools``, ``store``, ``workspace``, ``project``, ``runtime``, and ``tui``; none
of them may import it back, and ``tui`` must never see ``runtime`` directly —
the conversion between the two happens in :mod:`super_agent.app.tui_adapter`.

``LoadProjectInstructions`` is re-exported here even though its driver lives in
:mod:`super_agent.app.instructions`, so callers can keep writing
``app.LoadProjectInstructions``.
"""

from __future__ import annotations

# --- agents, workflows, and servers -----------------------------------------
from super_agent.app.agents import (
    AgentController as AgentController,
    AgentProfile as AgentProfile,
)
from super_agent.app.config import (
    AgentSettings as AgentSettings,
    Config as Config,
    DefaultSettings as DefaultSettings,
    Flags as Flags,
    LoadConfig as LoadConfig,
    LoadSettings as LoadSettings,
    LoadSettingsFile as LoadSettingsFile,
    LSPServerSettings as LSPServerSettings,
    MCPServerSettings as MCPServerSettings,
    PermissionSettings as PermissionSettings,
    SandboxSettings as SandboxSettings,
    SaveSettingsFile as SaveSettingsFile,
    Settings as Settings,
    SettingsPath as SettingsPath,
    TelemetrySettings as TelemetrySettings,
)
from super_agent.app.extensions import (
    Extensions as Extensions,
    ExtensionSettings as ExtensionSettings,
    PluginManifest as PluginManifest,
)
from super_agent.app.instructions import LoadProjectInstructions as LoadProjectInstructions
from super_agent.app.mcp import (
    MCPController as MCPController,
    MCPServerSummary as MCPServerSummary,
    NewMCPController as NewMCPController,
)

# --- the session constructors ------------------------------------------------
#
# Imported last: ``session`` reaches every other module here, so importing it
# after its dependencies keeps the chain readable top to bottom.
from super_agent.app.session import (
    NewSession as NewSession,
    NewSessionWithExtensions as NewSessionWithExtensions,
    NewSessionWithMCP as NewSessionWithMCP,
    waitPendingClosers as waitPendingClosers,
)
from super_agent.app.system_prompt import SystemPrompt as SystemPrompt
from super_agent.app.tui_adapter import (
    NewTUIConversation as NewTUIConversation,
    TUIConversation as TUIConversation,
)
from super_agent.app.workflows import WorkflowController as WorkflowController

__all__ = [
    "AgentController",
    "AgentProfile",
    "AgentSettings",
    "Config",
    "DefaultSettings",
    "ExtensionSettings",
    "Extensions",
    "Flags",
    "LSPServerSettings",
    "LoadConfig",
    "LoadProjectInstructions",
    "LoadSettings",
    "LoadSettingsFile",
    "MCPController",
    "MCPServerSettings",
    "MCPServerSummary",
    "NewMCPController",
    "NewSession",
    "NewSessionWithExtensions",
    "NewSessionWithMCP",
    "NewTUIConversation",
    "PermissionSettings",
    "PluginManifest",
    "SandboxSettings",
    "SaveSettingsFile",
    "Settings",
    "SettingsPath",
    "SystemPrompt",
    "TUIConversation",
    "TelemetrySettings",
    "WorkflowController",
    "waitPendingClosers",
]
