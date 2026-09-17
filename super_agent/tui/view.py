"""The app's view: the welcome block, the transcript, the footer, the help box.

Two invariants are the whole point of this module and both are testable without
pinning a rendered string:

* every line is clamped to the terminal width, so the terminal never hard-wraps;
* the dynamic area is windowed to the terminal height and shows its tail.

The clamp measures cells rather than characters: Rich's
:meth:`rich.text.Text.truncate` is width-aware on a styled value.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text

from super_agent.tui.app import App

__all__ = ["clampLines", "fitDynamicArea", "helpView", "view"]

#: The help box's widest and narrowest forms.
_HELP_MAX_WIDTH, _HELP_MIN_WIDTH = 64, 20


def clampLines(width: int, block: Text) -> Text:
    """Truncate every line of ``block`` to at most ``width`` cells."""
    limit = max(1, width)
    rendered = Text()
    for index, line in enumerate(block.split("\n", allow_blank=True)):
        if index:
            rendered.append("\n")
        if line.cell_len <= limit:
            rendered.append_text(line)
            continue
        truncated = line.copy()
        truncated.truncate(limit)
        rendered.append_text(truncated)
    return rendered


def fitDynamicArea(width: int, height: int, content: Text) -> Text:
    """Clamp ``content`` to the terminal and keep its last ``height`` lines."""
    clamped = clampLines(width, content)
    if height <= 0:
        return clamped
    lines = list(clamped.split("\n", allow_blank=True))
    if len(lines) <= height:
        return clamped
    return _join(lines[len(lines) - height :], "\n")


def view(app: App) -> Text:
    """The whole screen: the managed transcript and the footer under it."""
    if not app.ready:
        return Text("\n  Initializing...")
    if app.showHelp:
        return fitDynamicArea(app.width, app.height, helpView(app))
    parts: list[Text] = []
    content = app.transcript.view()
    if content.plain:
        parts.append(content)
    parts.append(_footerView(app))
    return fitDynamicArea(app.width, app.height, _join(parts, "\n\n"))


def helpView(app: App) -> Text:
    """The commands and shortcuts overlay, in a bordered box."""
    width = max(1, min(app.width, max(_HELP_MIN_WIDTH, min(_HELP_MAX_WIDTH, app.width - 4))))
    inner = max(1, width - 4)
    lines: list[Text] = [Text("Commands & Shortcuts", style=app.styles.accent_bold)]
    lines.append(Text(""))
    lines.extend(_nameLines(app, _COMMAND_HELP))
    lines.append(Text(""))
    lines.extend(Text(line) for line in _KEY_HELP)
    lines.append(Text(""))
    lines.extend(Text(line) for line in _APPROVAL_HELP)
    rendered = Text("╭" + "─" * max(0, width - 2) + "╮")
    for line in lines:
        rendered.append("\n")
        rendered.append("│ ")
        rendered.append_text(_fit(line, inner))
        rendered.append(" │")
    rendered.append("\n")
    rendered.append("╰" + "─" * max(0, width - 2) + "╯")
    return rendered


def _nameLines(app: App, items: Sequence[tuple[str, str]]) -> list[Text]:
    """Help rows whose first column is a styled command name."""
    lines: list[Text] = []
    for name, description in items:
        line = Text(name, style=app.styles.accent_bold)
        line.append(" " * max(1, 13 - len(name)) + description)
        lines.append(line)
    return lines


def _fit(line: Text, width: int) -> Text:
    """``line`` padded or truncated to exactly ``width`` cells."""
    if line.cell_len > width:
        truncated = line.copy()
        truncated.truncate(width)
        return truncated
    padded = line.copy()
    padded.append(" " * (width - line.cell_len))
    return padded


def _join(parts: Sequence[Text], separator: str) -> Text:
    """Join rendered fragments, keeping their styles."""
    rendered = Text()
    for index, part in enumerate(parts):
        if index:
            rendered.append(separator)
        rendered.append_text(part)
    return rendered


def _footerView(app: App) -> Text:
    """Deferred: the package surface imports this module, so it is patched in late."""
    from super_agent.tui import footerView

    return footerView(app)


#: The command rows the overlay lists, matching the help text.
_COMMAND_HELP: tuple[tuple[str, str], ...] = (
    ("/agent", "Select agent profile"),
    ("/attach", "Queue image or file"),
    ("/clear", "Reset conversation"),
    ("/sessions", "List saved sessions"),
    ("/resume", "Resume saved session"),
    ("/compact", "Compact context"),
    ("/undo", "Restore checkpoint"),
    ("/permissions", "Show permission policy"),
    ("/mcp", "Manage MCP servers"),
    ("/review", "Review current changes"),
    ("/export", "Export or share session"),
    ("/help", "Show this menu"),
    ("/quit", "Exit application"),
)

_KEY_HELP: tuple[str, ...] = (
    "enter        Submit / steer active turn",
    "tab          Queue while running",
    "ctrl+j       Insert newline",
    "up/down      History / move lines",
    "tab          Complete slash command",
    "up/down      Select slash command",
    "esc/ctrl+u   Clear input / Cancel",
    "ctrl+l       Clear screen",
    "ctrl+y       Copy last code block",
    "ctrl+o       Toggle latest tools",
    "alt+o        Toggle all tools",
    "ctrl+t       Toggle latest reasoning",
    "alt+t        Toggle all reasoning",
    "ctrl+c       Quit / Cancel",
    "?            Toggle help",
)

_APPROVAL_HELP: tuple[str, ...] = (
    "Tool Approval:",
    "up/down      Select decision",
    "enter        Confirm decision",
    "1/y          Approve once",
    "2/a          Always allow",
    "3/n          Deny call",
)
