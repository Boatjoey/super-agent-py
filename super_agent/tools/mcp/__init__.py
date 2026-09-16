"""The stdio MCP client adapter.

Ported from the Go ``tools/mcp`` package. Python splits a Go package across
modules, so this module stands in for the package namespace: everything Go
callers write as ``mcp.X`` is re-exported here.

Discovered tools join ``tools.Registry`` atomically and are always risky under
the common permission policy, regardless of what a server claims about itself.
"""

from __future__ import annotations

from super_agent.tools.mcp.client import (
    Connect as Connect,
    Manager as Manager,
    RemoteTool as RemoteTool,
    ServerConfig as ServerConfig,
    ServerInfo as ServerInfo,
)
