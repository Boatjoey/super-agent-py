"""The status line: the configured items, composed into one row.

``docs/tui.md#layout`` gives the root this row — it is cross-feature, so it
belongs to no feature package — and ``docs/config.md#tui`` gives it its
vocabulary: ``tui.status_line`` is an ordered list of item names, drawn left to
right from :data:`ITEMS`. The key's three values mean three things: absent draws
:data:`DEFAULT_ORDER`, ``null`` removes the row, and a list draws exactly the
items it names.

Two rules the composition keeps, both from the specification:

* An item whose data is unavailable is omitted rather than drawn empty, so a
  model name the process never learned costs neither text nor a separator.
* Nothing here divides by a context-window size, because the runtime carries
  none. ``context_usage`` shows the counts the provider reported, and no
  percentage.

The module is pure: it reads the model and returns text. It mounts no widget,
calls no port, and measures no terminal, so the whole vocabulary is testable
without one. Clipping the row to the terminal width, and hiding it when
``tui.status_line`` is ``null``, belong to the widget that owns the layout.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from rich.text import Text

from super_agent.tui.app import App, displayCWD
from super_agent.tui.conversation import ContextUsage

__all__ = ["DEFAULT_ORDER", "ITEMS", "SEPARATOR", "compose", "order"]

#: The item vocabulary of ``docs/config.md#tui``, spelled as the document lists it.
ITEMS: Final[tuple[str, ...]] = (
    "model",
    "approval",
    "context_usage",
    "session_id",
    "sandbox",
    "cwd",
    "spinner",
)

#: What the row draws when ``tui.status_line`` is absent.
DEFAULT_ORDER: Final[tuple[str, ...]] = ("model", "approval", "context_usage")

#: What sits between two drawn items. Secondary text, like the metadata it separates.
SEPARATOR: Final[str] = " · "

#: One item's renderer: the text to draw, or ``None`` when its data is unavailable.
type _Renderer = Callable[[App], Text | None]


def order(status_line: tuple[str, ...] | None) -> tuple[str, ...]:
    """The items to draw, in order: the setting, or the default when it is absent."""
    return DEFAULT_ORDER if status_line is None else status_line


def compose(app: App) -> Text:
    """The status row: every configured item that has data, drawn left to right.

    The item names are the ones the configuration validated, so an unknown name
    is a programming error rather than a user error and is left to raise.
    """
    rendered = Text()
    drawn = False
    for name in order(app.info.status_line):
        item = _RENDERERS[name](app)
        if item is None:
            continue
        if drawn:
            rendered.append(SEPARATOR, style=app.styles.secondary)
        rendered.append_text(item)
        drawn = True
    return rendered


def _model(app: App) -> Text | None:
    """The model the active profile runs."""
    return _metadata(app, app.info.model_name)


def _approval(app: App) -> Text | None:
    """The session's permission mode."""
    return _metadata(app, app.info.permission_mode)


def _context_usage(app: App) -> Text | None:
    """What the provider reported the most recent model call cost, in tokens."""
    usage = app.contextUsage
    if usage is None or not _reported(usage):
        return None
    return _metadata(app, f"↑{_tokens(usage.input_tokens)} ↓{_tokens(usage.output_tokens)}")


def _session_id(app: App) -> Text | None:
    """The active session's id."""
    return _metadata(app, app.info.session_id)


def _sandbox(app: App) -> Text | None:
    """How commands are contained: ``strict``, or ``off``."""
    return _metadata(app, app.info.sandbox)


def _cwd(app: App) -> Text | None:
    """The working directory, abbreviated to ``~`` under the home directory."""
    return _metadata(app, displayCWD(app.info.cwd))


def _spinner(app: App) -> Text | None:
    """What the agent is doing.

    The distinction the item carries is the one the user acts on: idle is dim
    like the rest of the row, work in progress is the accent, and waiting on the
    user is the accent at its boldest, because nothing moves until they answer.
    """
    status = app.agentStatus
    if status.label == "":
        return None
    if status.awaiting_approval:
        style = app.styles.accent_bold
    elif status.busy:
        style = app.styles.accent
    else:
        style = app.styles.secondary
    return Text(status.label, style=style)


#: One renderer per item of :data:`ITEMS`; ``test_every_item_is_rendered`` holds
#: the two in step, because a missing entry would read as an ill-formed key.
_RENDERERS: Final[dict[str, _Renderer]] = {
    "model": _model,
    "approval": _approval,
    "context_usage": _context_usage,
    "session_id": _session_id,
    "sandbox": _sandbox,
    "cwd": _cwd,
    "spinner": _spinner,
}


def _metadata(app: App, value: str) -> Text | None:
    """A dim item, or nothing at all when there is no value to draw."""
    if value == "":
        return None
    return Text(value, style=app.styles.secondary)


def _reported(usage: ContextUsage) -> bool:
    """Whether a usage report says anything: an all-zero one is not a report."""
    return usage.input_tokens > 0 or usage.output_tokens > 0 or usage.total_tokens > 0


def _tokens(value: int) -> str:
    """A token count in the compact form one row has room for."""
    if value < 1000:
        return str(value)
    if value < 1_000_000:
        return _scaled(value / 1000, "k")
    return _scaled(value / 1_000_000, "M")


def _scaled(value: float, suffix: str) -> str:
    """One decimal, minus the trailing zero: ``1.0k`` reads as ``1k``."""
    return f"{value:.1f}".removesuffix(".0") + suffix
