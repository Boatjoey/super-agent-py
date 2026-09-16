"""The narrow ports the command feature needs, one per use case.

Each port is declared here and implemented at the composition boundary; the
feature never learns what is behind it. Reads are synchronous, and every call
that can touch the disk, the network, or another process is ``async``: it is
awaited rather than run inline.
"""

from __future__ import annotations

import dataclasses
from typing import Protocol

__all__ = [
    "AgentPort",
    "AgentSummary",
    "Attachment",
    "ExtensionPort",
    "MCPPort",
    "MCPServerSummary",
    "MemoryPort",
    "PermissionPort",
    "Ports",
    "SessionPort",
    "SessionSummary",
    "WorkspacePort",
]


@dataclasses.dataclass(frozen=True, slots=True)
class SessionSummary:
    """One saved session, for listing and resuming."""

    id: str = ""
    title: str = ""
    provider: str = ""
    model: str = ""
    cwd: str = ""
    parent_id: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class MCPServerSummary:
    """One configured MCP server and the tools it discovered."""

    name: str = ""
    tools: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class AgentSummary:
    """One agent profile."""

    name: str = ""
    provider: str = ""
    model: str = ""
    permission_mode: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Attachment:
    """A file queued for the next turn, as a command reports it."""

    name: str = ""
    mime: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Ports:
    """Every capability the command feature needs."""

    sessions: SessionPort
    permissions: PermissionPort
    mcp: MCPPort
    agents: AgentPort
    memory: MemoryPort
    workspace: WorkspacePort
    extensions: ExtensionPort


class SessionPort(Protocol):
    """The conversation lifecycle commands operate on."""

    async def reset(self) -> None: ...

    async def list_sessions(self) -> list[SessionSummary]: ...

    async def resume(self, session_id: str) -> None: ...

    async def rename_session(self, session_id: str, title: str) -> None: ...

    async def delete_session(self, session_id: str) -> None: ...

    async def compact(self, summary: str) -> None: ...

    async def undo(self) -> None: ...

    async def fork(self, title: str) -> str: ...

    async def export(self, format: str) -> str: ...


class PermissionPort(Protocol):
    """Reads and changes the active permission policy.

    Mode and approval are read back from the runtime, so the display cannot
    drift from the behaviour it describes.
    """

    async def set_permission_mode(self, mode: str) -> None: ...

    def permission_mode(self) -> str: ...

    def auto_approve_tools(self) -> bool: ...


class MCPPort(Protocol):
    """Manages MCP server lifecycle."""

    def list_mcp_servers(self) -> list[MCPServerSummary]: ...

    async def add_mcp_server(self, name: str, command: str, args: list[str]) -> None: ...

    async def remove_mcp_server(self, name: str) -> None: ...

    async def restart_mcp_server(self, name: str) -> None: ...


class AgentPort(Protocol):
    """Lists and selects agent profiles."""

    def list_agents(self) -> list[AgentSummary]: ...

    def current_agent(self) -> AgentSummary: ...

    async def use_agent(self, name: str) -> None: ...


class MemoryPort(Protocol):
    """Reads and writes cross-session memory."""

    async def memories(self) -> list[str]: ...

    async def remember(self, text: str) -> None: ...

    async def forget_memories(self) -> None: ...


class WorkspacePort(Protocol):
    """Runs read-only repository and language-server queries."""

    async def git_diff(self) -> str: ...

    async def git_status(self) -> str: ...

    async def diagnostics(self, path: str) -> str: ...


class ExtensionPort(Protocol):
    """Exposes discovered commands, skills, and plugins."""

    def custom_commands(self) -> list[str]: ...

    async def expand_custom_command(self, name: str, arguments: str) -> str: ...

    def skills(self) -> list[str]: ...

    def plugins(self) -> list[str]: ...
