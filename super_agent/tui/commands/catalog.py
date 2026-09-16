"""The slash-command catalogue.

The built-ins come first, in the order the composer offers them, followed by the
discovered custom commands in sorted order.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from super_agent.tui.commands.model import Model

__all__ = ["Command", "is_command", "palette", "slashCommandDescriptions", "slashCommands"]


@dataclasses.dataclass(frozen=True, slots=True)
class Command:
    """One palette entry: the name the composer completes and the hint it shows."""

    name: str = ""
    description: str = ""


#: The built-in commands, in palette order.
slashCommands: tuple[str, ...] = (
    "/clear",
    "/compact",
    "/delete-session",
    "/help",
    "/instructions",
    "/permissions",
    "/mcp",
    "/quit",
    "/rename",
    "/reset",
    "/resume",
    "/sessions",
    "/undo",
    "/agent",
    "/build",
    "/plan",
    "/mode",
    "/fork",
    "/memory",
    "/remember",
    "/forget",
    "/review",
    "/diff",
    "/fix-ci",
    "/branch",
    "/commit-message",
    "/export",
    "/share",
    "/attach",
    "/attachments",
    "/commands",
    "/skills",
    "/plugins",
    "/diagnostics",
)

#: What each built-in does, as the palette shows it.
slashCommandDescriptions: dict[str, str] = {
    "/clear": "Reset the conversation",
    "/agent": "List or select an agent <name>",
    "/attach": "Attach a workspace file <path>",
    "/attachments": "List pending attachments",
    "/build": "Switch to the build agent",
    "/branch": "Show branch and working-tree status",
    "/commit-message": "Suggest a commit message",
    "/commands": "List custom commands",
    "/compact": "Compact context [summary]",
    "/delete-session": "Delete a saved session <id>",
    "/diff": "Preview the current patch",
    "/diagnostics": "Show LSP diagnostics <path>",
    "/export": "Export session <markdown|json>",
    "/fix-ci": "Inspect and fix failing CI checks",
    "/help": "Show commands and shortcuts",
    "/fork": "Fork the current transcript [title]",
    "/forget": "Clear cross-session memory",
    "/instructions": "Show loaded instruction files",
    "/mcp": "Manage MCP servers <list|add|remove|restart>",
    "/mode": "Switch mode <plan|build>",
    "/memory": "Show cross-session memory",
    "/permissions": "Inspect or change permission mode",
    "/plugins": "List loaded plugins",
    "/plan": "Switch to the plan agent",
    "/quit": "Exit Super Agent",
    "/rename": "Rename a session <id> <title>",
    "/remember": "Add cross-session memory <text>",
    "/reset": "Reset the conversation",
    "/review": "Review the current changes",
    "/resume": "Resume a saved session <id>",
    "/sessions": "List saved sessions",
    "/share": "Create a local HTML share file",
    "/skills": "List loaded skills",
    "/undo": "Restore the last checkpoint",
}


def is_command(text: str) -> bool:
    """Whether submitted text is a slash command rather than a prompt.

    A pure predicate, so callers can route input without holding command state.
    """
    return text.startswith("/")


def palette(model: Model) -> tuple[Command, ...]:
    """The built-ins followed by the discovered custom commands."""
    commands = [Command(name=name, description=slashCommandDescriptions[name]) for name in slashCommands]
    for name in sorted(model.customCommands):
        commands.append(Command(name="/" + name.removeprefix("/"), description="Custom command"))
    return tuple(commands)
