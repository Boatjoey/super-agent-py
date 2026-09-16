"""The narrow ports the command feature needs, one per use case.

Ported from the Go ``tui/commands/ports.go``. Each port is declared here and
implemented at the composition boundary; the feature never learns what is behind
it. Reads are synchronous the way Go's are, and every call that can touch the
disk, the network, or another process is ``async`` — Python has no goroutine to
run it in, so the adapter must be awaited.
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

    ID: str = ""
    Title: str = ""
    Provider: str = ""
    Model: str = ""
    CWD: str = ""
    ParentID: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class MCPServerSummary:
    """One configured MCP server and the tools it discovered."""

    Name: str = ""
    Tools: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class AgentSummary:
    """One agent profile."""

    Name: str = ""
    Provider: str = ""
    Model: str = ""
    PermissionMode: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Attachment:
    """A file queued for the next turn, as a command reports it."""

    Name: str = ""
    MIME: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Ports:
    """Every capability the command feature needs."""

    Sessions: SessionPort
    Permissions: PermissionPort
    MCP: MCPPort
    Agents: AgentPort
    Memory: MemoryPort
    Workspace: WorkspacePort
    Extensions: ExtensionPort


class SessionPort(Protocol):
    """The conversation lifecycle commands operate on."""

    async def Reset(self) -> None: ...

    async def ListSessions(self) -> list[SessionSummary]: ...

    async def Resume(self, session_id: str) -> None: ...

    async def RenameSession(self, session_id: str, title: str) -> None: ...

    async def DeleteSession(self, session_id: str) -> None: ...

    async def Compact(self, summary: str) -> None: ...

    async def Undo(self) -> None: ...

    async def Fork(self, title: str) -> str: ...

    async def Export(self, format: str) -> str: ...


class PermissionPort(Protocol):
    """Reads and changes the active permission policy.

    Mode and approval are read back from the runtime, so the display cannot
    drift from the behaviour it describes.
    """

    async def SetPermissionMode(self, mode: str) -> None: ...

    def PermissionMode(self) -> str: ...

    def AutoApproveTools(self) -> bool: ...


class MCPPort(Protocol):
    """Manages MCP server lifecycle."""

    def ListMCPServers(self) -> list[MCPServerSummary]: ...

    async def AddMCPServer(self, name: str, command: str, args: list[str]) -> None: ...

    async def RemoveMCPServer(self, name: str) -> None: ...

    async def RestartMCPServer(self, name: str) -> None: ...


class AgentPort(Protocol):
    """Lists and selects agent profiles."""

    def ListAgents(self) -> list[AgentSummary]: ...

    def CurrentAgent(self) -> AgentSummary: ...

    async def UseAgent(self, name: str) -> None: ...


class MemoryPort(Protocol):
    """Reads and writes cross-session memory."""

    async def Memories(self) -> list[str]: ...

    async def Remember(self, text: str) -> None: ...

    async def ForgetMemories(self) -> None: ...


class WorkspacePort(Protocol):
    """Runs read-only repository and language-server queries."""

    async def GitDiff(self) -> str: ...

    async def GitStatus(self) -> str: ...

    async def Diagnostics(self, path: str) -> str: ...


class ExtensionPort(Protocol):
    """Exposes discovered commands, skills, and plugins."""

    def CustomCommands(self) -> list[str]: ...

    async def ExpandCustomCommand(self, name: str, arguments: str) -> str: ...

    def Skills(self) -> list[str]: ...

    def Plugins(self) -> list[str]: ...
