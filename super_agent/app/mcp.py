"""MCP server lifecycle, dynamic tool registration, and settings persistence.

Every mutation is a three-step transaction — change the runtime, change the
registry, persist — and a failure at any step restores the ones before it, so the
running session and ``settings.json`` never disagree about which servers exist.

Persistence re-reads the settings file before writing it, so a concurrent edit
outside the app is not silently overwritten.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
from collections.abc import Mapping, Sequence

from super_agent import tools
from super_agent.app.config import MCPServerSettings, load_settings_file, save_settings_file
from super_agent.errors import JoinedError
from super_agent.runtime.protocol.run_context import RunContext, live_context
from super_agent.tools import mcp as mcptools

__all__ = [
    "MCPController",
    "MCPServerSummary",
    "cloneMCPSettings",
    "new_mcp_controller",
    "toolNames",
]


@dataclasses.dataclass(frozen=True, slots=True)
class MCPServerSummary:
    """One configured server and the tools it contributed."""

    name: str = ""
    tools: tuple[str, ...] = ()


class MCPController:
    """The application-facing MCP manager.

    The lock is an :class:`asyncio.Lock` and is held across the whole operation:
    connecting, registering, and persisting have to be atomic together or a
    rollback could undo somebody else's change.
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

    def list(self) -> list[MCPServerSummary]:
        """Every connected server, ordered by name."""
        return [MCPServerSummary(name=server.name, tools=tuple(server.tools)) for server in self.manager.servers()]

    async def add(self, ctx: RunContext, name: str, command: str, args: Sequence[str]) -> None:
        """Connect a server, install its tools, and persist it."""
        async with self._lock:
            if name == "" or command == "":
                raise ValueError("MCP server name and command are required")
            settings = MCPServerSettings(command=command, args=tuple(args))
            config = self.serverConfig(name, settings)
            added = await self.manager.add(ctx, config)
            try:
                self.registry.add(*added)
            except BaseException:
                await self.manager.remove(name)
                raise
            try:
                self.persist(name, settings)
            except BaseException:
                self.registry.remove(*toolNames(added))
                await self.manager.remove(name)
                raise
            self.configs[name] = settings

    async def remove(self, name: str) -> None:
        """Disconnect a server, drop its tools, and forget it in settings.

        A persistence failure reconnects the server rather than leaving the
        session without one settings.json says it has.
        """
        async with self._lock:
            settings = self.configs.get(name)
            if settings is None:
                raise ValueError("MCP server not found: " + name)
            names = await self.manager.remove(name)
            self.registry.remove(*names)
            try:
                self.persist(name, None)
            except BaseException as error:
                reconnectError: BaseException | None = None
                try:
                    added = await self.manager.add(live_context(), self.serverConfig(name, settings))
                    self.registry.add(*added)
                except BaseException as failure:
                    reconnectError = failure
                raise JoinedError(error, reconnectError) from None
            del self.configs[name]

    async def restart(self, ctx: RunContext, name: str) -> None:
        """Reconnect a server, replacing its tools in the registry."""
        async with self._lock:
            await self.manager.restart(
                ctx,
                name,
                lambda oldNames, replacement: self.registry.replace(oldNames, *replacement),
            )

    def persist(self, name: str, server: MCPServerSettings | None) -> None:
        """Write one server's settings, or forget it, without touching the rest."""
        settings = load_settings_file(self.settingsPath)
        servers = dict(settings.mcp_servers)
        if server is None:
            servers.pop(name, None)
        else:
            servers[name] = server
        save_settings_file(self.settingsPath, dataclasses.replace(settings, mcp_servers=servers))

    def serverConfig(self, name: str, settings: MCPServerSettings) -> mcptools.ServerConfig:
        """The connect-time configuration for one server, with its cwd resolved."""
        cwd = settings.cwd
        if cwd == "":
            cwd = self.workspace
        elif not os.path.isabs(cwd):
            cwd = os.path.join(self.workspace, cwd)
        return mcptools.ServerConfig(
            name=name,
            command=settings.command,
            args=list(settings.args),
            env=dict(settings.env),
            cwd=cwd,
            connect_timeout=float(settings.connect_timeout_seconds),
            call_timeout=float(settings.call_timeout_seconds),
        )


def new_mcp_controller(
    manager: mcptools.Manager,
    registry: tools.Registry,
    settingsPath: str,
    workspace: str,
    configs: Mapping[str, MCPServerSettings] | None,
) -> MCPController:
    """The controller over ``manager``."""
    return MCPController(manager, registry, settingsPath, workspace, configs)


def cloneMCPSettings(configs: Mapping[str, MCPServerSettings] | None) -> dict[str, MCPServerSettings]:
    """A mutable copy, so the controller never writes through a caller's map."""
    return {} if configs is None else dict(configs)


def toolNames(items: Sequence[mcptools.RemoteTool]) -> list[str]:
    """The spec names a batch of discovered tools registers under."""
    names: list[str] = []
    for item in items:
        specs = item.specs()
        if specs:
            names.append(specs[0].name)
    return names
