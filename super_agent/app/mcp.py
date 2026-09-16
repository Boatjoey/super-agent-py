"""MCP server lifecycle, dynamic tool registration, and settings persistence.

Ported from ``app/mcp.go``. Every mutation is a three-step transaction — change
the runtime, change the registry, persist — and a failure at any step restores
the ones before it, so the running session and ``settings.json`` never disagree
about which servers exist.

Persistence re-reads the settings file before writing it, so a concurrent edit
outside the app is not silently overwritten.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from collections.abc import Mapping, Sequence

from super_agent import tools
from super_agent.app.config import LoadSettingsFile, MCPServerSettings, SaveSettingsFile
from super_agent.errors import JoinedError
from super_agent.runtime.protocol.run_context import LiveContext, RunContext
from super_agent.tools import mcp as mcptools

__all__ = [
    "MCPController",
    "MCPServerSummary",
    "NewMCPController",
    "cloneMCPSettings",
    "toolNames",
]


@dataclasses.dataclass(frozen=True, slots=True)
class MCPServerSummary:
    """One configured server and the tools it contributed."""

    Name: str = ""
    Tools: tuple[str, ...] = ()


class MCPController:
    """The application-facing MCP manager.

    The lock is an :class:`asyncio.Lock` and is held across the whole operation,
    exactly as Go holds its mutex: connecting, registering, and persisting have to
    be atomic together or a rollback could undo somebody else's change.
    """

    __slots__ = ("_lock", "configs", "manager", "registry", "settingsPath", "workspace")

    def __init__(
        self,
        manager: mcptools.Manager,
        registry: tools.Registry,
        settingsPath: str,
        workspace: str,
        configs: Mapping[str, MCPServerSettings] | None,
    ) -> None:
        self.manager = manager
        self.registry = registry
        self.settingsPath = settingsPath
        self.workspace = workspace
        self.configs = cloneMCPSettings(configs)
        self._lock = asyncio.Lock()

    def List(self) -> list[MCPServerSummary]:
        """Every connected server, ordered by name."""
        return [MCPServerSummary(Name=server.Name, Tools=tuple(server.Tools)) for server in self.manager.Servers()]

    async def Add(self, ctx: RunContext, name: str, command: str, args: Sequence[str]) -> None:
        """Connect a server, install its tools, and persist it."""
        async with self._lock:
            if name == "" or command == "":
                raise ValueError("MCP server name and command are required")
            settings = MCPServerSettings(Command=command, Args=tuple(args))
            config = self.serverConfig(name, settings)
            added = await self.manager.Add(ctx, config)
            try:
                self.registry.Add(*added)
            except BaseException:
                await self.manager.Remove(name)
                raise
            try:
                self.persist(name, settings)
            except BaseException:
                self.registry.Remove(*toolNames(added))
                await self.manager.Remove(name)
                raise
            self.configs[name] = settings

    async def Remove(self, name: str) -> None:
        """Disconnect a server, drop its tools, and forget it in settings.

        A persistence failure reconnects the server rather than leaving the
        session without one settings.json says it has.
        """
        async with self._lock:
            settings = self.configs.get(name)
            if settings is None:
                raise ValueError("MCP server not found: " + name)
            names = await self.manager.Remove(name)
            self.registry.Remove(*names)
            try:
                self.persist(name, None)
            except BaseException as error:
                reconnectError: BaseException | None = None
                try:
                    added = await self.manager.Add(LiveContext(), self.serverConfig(name, settings))
                    self.registry.Add(*added)
                except BaseException as failure:
                    reconnectError = failure
                raise JoinedError(error, reconnectError) from None
            del self.configs[name]

    async def Restart(self, ctx: RunContext, name: str) -> None:
        """Reconnect a server, replacing its tools in the registry."""
        async with self._lock:
            await self.manager.Restart(
                ctx,
                name,
                lambda oldNames, replacement: self.registry.Replace(oldNames, *replacement),
            )

    def persist(self, name: str, server: MCPServerSettings | None) -> None:
        """Write one server's settings, or forget it, without touching the rest."""
        settings = LoadSettingsFile(self.settingsPath)
        servers = dict(settings.MCPServers)
        if server is None:
            servers.pop(name, None)
        else:
            servers[name] = server
        SaveSettingsFile(self.settingsPath, dataclasses.replace(settings, MCPServers=servers))

    def serverConfig(self, name: str, settings: MCPServerSettings) -> mcptools.ServerConfig:
        """The connect-time configuration for one server, with its cwd resolved."""
        cwd = settings.CWD
        if cwd == "":
            cwd = self.workspace
        elif not os.path.isabs(cwd):
            cwd = os.path.join(self.workspace, cwd)
        return mcptools.ServerConfig(
            Name=name,
            Command=settings.Command,
            Args=list(settings.Args),
            Env=dict(settings.Env),
            CWD=cwd,
            ConnectTimeout=float(settings.ConnectTimeoutSeconds),
            CallTimeout=float(settings.CallTimeoutSeconds),
        )


def NewMCPController(
    manager: mcptools.Manager,
    registry: tools.Registry,
    settingsPath: str,
    workspace: str,
    configs: Mapping[str, MCPServerSettings] | None,
) -> MCPController:
    """The controller over ``manager``, mirroring Go's ``NewMCPController``."""
    return MCPController(manager, registry, settingsPath, workspace, configs)


def cloneMCPSettings(configs: Mapping[str, MCPServerSettings] | None) -> dict[str, MCPServerSettings]:
    """A mutable copy, so the controller never writes through a caller's map."""
    return {} if configs is None else dict(configs)


def toolNames(items: Sequence[mcptools.RemoteTool]) -> list[str]:
    """The spec names a batch of discovered tools registers under."""
    names: list[str] = []
    for item in items:
        specs = item.Specs()
        if specs:
            names.append(specs[0].Name)
    return names
