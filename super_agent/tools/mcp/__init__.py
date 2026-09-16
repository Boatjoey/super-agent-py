"""The stdio MCP client adapter.

The mcp package surface: everything callers write as ``mcp.X`` is re-exported
here.

Discovered tools join ``tools.Registry`` atomically and are always risky under
the common permission policy, regardless of what a server claims about itself.
"""

from __future__ import annotations

from super_agent.tools.mcp.client import (
    Manager as Manager,
    RemoteTool as RemoteTool,
    ServerConfig as ServerConfig,
    ServerInfo as ServerInfo,
    connect as connect,
)
