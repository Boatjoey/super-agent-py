"""The command feature's output formatting.

Everything here is a pure function from a port's answer to the text the user
reads in scrollback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from super_agent.tui.commands.model import Config
    from super_agent.tui.commands.ports import MCPServerSummary, SessionSummary

__all__ = [
    "divider",
    "formatInstructions",
    "formatMCPServers",
    "formatNamedItems",
    "formatPermissions",
    "formatSessions",
]


def divider(label: str) -> str:
    """A ``── label ──`` separator for scrollback."""
    return "── " + label + " ──"


def formatSessions(summaries: list[SessionSummary]) -> str:
    """One row per saved session."""
    if not summaries:
        return "No saved sessions"
    lines: list[str] = []
    for summary in summaries:
        parent = f"  child-of:{summary.ParentID}" if summary.ParentID else ""
        lines.append(f"{summary.ID}  {summary.Title}  {summary.Provider}/{summary.Model}{parent}")
    return "\n".join(lines)


def formatMCPServers(servers: list[MCPServerSummary]) -> str:
    """One row per configured MCP server."""
    if not servers:
        return "No MCP servers"
    lines: list[str] = []
    for server in servers:
        tools = ", ".join(server.Tools) or "no tools"
        lines.append(f"{server.Name}  {tools}")
    return "\n".join(lines)


def formatInstructions(paths: tuple[str, ...]) -> str:
    """The instruction sources that were loaded, in load order."""
    if not paths:
        return "No instruction files loaded"
    return "Loaded instruction sources:\n" + "\n".join(f"- {path}" for path in paths)


def formatPermissions(config: Config, mode: str, autoApprove: bool) -> str:
    """The runtime's own policy values, so the display cannot drift from them."""
    return (
        f"Permission mode: {mode or 'ask'}\n"
        f"Tools: {_onOff(not config.NoTools)}\n"
        f"Approval: {_onOff(autoApprove)}\n"
        f"CWD: {config.CWD}"
    )


def formatNamedItems(label: str, items: list[str]) -> str:
    """A titled bullet list, or the empty case."""
    if not items:
        return "No " + label.lower()
    return label + ":\n- " + "\n- ".join(items)


def _onOff(enabled: bool) -> str:
    return "on" if enabled else "off"
