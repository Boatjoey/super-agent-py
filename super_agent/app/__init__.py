"""The composition root's public surface.

The app package surface: configuration and settings, the session and agent
constructors, the MCP and workflow controllers, and the terminal adapter. Callers
write ``app.load_config``; the re-exports here keep that spelling.

``app`` is the only place the concrete adapters meet. It may import ``llm``,
``tools``, ``store``, ``workspace``, ``project``, ``runtime``, and ``tui``; none
of them may import it back, and ``tui`` must never see ``runtime`` directly —
the conversion between the two happens in :mod:`super_agent.app.tui_adapter`.

``load_project_instructions`` is re-exported here even though its driver lives in
:mod:`super_agent.app.instructions`, so callers can keep writing
``app.load_project_instructions``.
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
    Flags as Flags,
    LSPServerSettings as LSPServerSettings,
    MCPServerSettings as MCPServerSettings,
    PermissionSettings as PermissionSettings,
    SandboxSettings as SandboxSettings,
    Settings as Settings,
    TelemetrySettings as TelemetrySettings,
    default_settings as default_settings,
    load_config as load_config,
    load_settings as load_settings,
    load_settings_file as load_settings_file,
    save_settings_file as save_settings_file,
    settings_path as settings_path,
)
from super_agent.app.extensions import (
    Extensions as Extensions,
    ExtensionSettings as ExtensionSettings,
    PluginManifest as PluginManifest,
)
from super_agent.app.instructions import load_project_instructions as load_project_instructions
from super_agent.app.mcp import (
    MCPController as MCPController,
    MCPServerSummary as MCPServerSummary,
    new_mcp_controller as new_mcp_controller,
)

# --- the session constructors ------------------------------------------------
#
# Imported last: ``session`` reaches every other module here, so importing it
# after its dependencies keeps the chain readable top to bottom.
from super_agent.app.session import (
    new_session as new_session,
    new_session_with_extensions as new_session_with_extensions,
    new_session_with_mcp as new_session_with_mcp,
    waitPendingClosers as waitPendingClosers,
)
from super_agent.app.system_prompt import SYSTEM_PROMPT as SYSTEM_PROMPT
from super_agent.app.tui_adapter import (
    TUIConversation as TUIConversation,
    new_tui_conversation as new_tui_conversation,
)
from super_agent.app.workflows import WorkflowController as WorkflowController

__all__ = [
    "SYSTEM_PROMPT",
    "AgentController",
    "AgentProfile",
    "AgentSettings",
    "Config",
    "ExtensionSettings",
    "Extensions",
    "Flags",
    "LSPServerSettings",
    "MCPController",
    "MCPServerSettings",
    "MCPServerSummary",
    "PermissionSettings",
    "PluginManifest",
    "SandboxSettings",
    "Settings",
    "TUIConversation",
    "TelemetrySettings",
    "WorkflowController",
    "default_settings",
    "load_config",
    "load_project_instructions",
    "load_settings",
    "load_settings_file",
    "new_mcp_controller",
    "new_session",
    "new_session_with_extensions",
    "new_session_with_mcp",
    "new_tui_conversation",
    "save_settings_file",
    "settings_path",
    "waitPendingClosers",
]
