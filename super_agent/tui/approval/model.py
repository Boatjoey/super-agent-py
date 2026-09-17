"""The pending tool-approval request, its selection, and the decision it yields.

The model owns the request, the selected row, and the ``submitted`` latch; the
root owns the decision channel the answer travels on, so this feature never talks
to a port.
"""

from __future__ import annotations

import dataclasses
from typing import Final

from rich.style import Style
from rich.text import Text

__all__ = [
    "APPROVE_ALWAYS",
    "APPROVE_ONCE",
    "DENY",
    "Decision",
    "Model",
    "Request",
    "Styles",
    "default_styles",
]

#: The three answers, spelled as the runtime spells them.
_DECISIONS_BY_ROW: Final[tuple[str, ...]] = ("once", "always", "deny")

#: The selected-row marker, U+203A.
_SELECTED_MARKER = "\u203a"
#: Emphasis is not colour, so the menu keeps it: the palette owns colour only.
_BOLD = Style(bold=True)


@dataclasses.dataclass(frozen=True, slots=True)
class Styles:
    """The roles the approval menu renders with.

    The shape is the feature's; the values are the root's palette, handed over
    at construction, because a feature may not reach for the root's styles (R6).
    """

    banner: Style
    dim: Style
    selected: Style
    accent: Style


def default_styles() -> Styles:
    """The menu's own defaults, used when a model is built bare.

    No colour: the root builds the real styles from the palette and passes them
    to :class:`Model`.
    """
    return Styles(banner=Style(), dim=Style(), selected=Style(), accent=Style())


class Decision(str):
    """The answer the runtime receives for one tool call."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Decision({str.__repr__(self)})"


APPROVE_ONCE: Final[Decision] = Decision("once")
APPROVE_ALWAYS: Final[Decision] = Decision("always")
DENY: Final[Decision] = Decision("deny")


@dataclasses.dataclass(frozen=True, slots=True)
class Request:
    """One pending permission request, as the menu displays it."""

    tool_name: str = ""
    input: str = ""
    command_class: str = ""
    cwd: str = ""
    touched_paths: tuple[str, ...] = ()
    reason: str = ""
    batch_index: int = 0
    batch_total: int = 0


@dataclasses.dataclass(slots=True)
class Model:
    """The approval menu's state. One menu is open at a time."""

    request: Request | None = None
    selection: int = 0
    submitted: bool = False
    styles: Styles = dataclasses.field(default_factory=default_styles)

    def active(self) -> bool:
        """Whether a request is waiting for an answer."""
        return self.request is not None

    def open(self, request: Request) -> None:
        """Show ``request``, forget any previous selection and latch."""
        self.request = request
        self.selection = 0
        self.submitted = False

    def clear(self) -> None:
        """Close the menu. A closed menu is not active and not submitted."""
        self.request = None
        self.selection = 0
        self.submitted = False

    def update(self, key: str) -> tuple[Model, Decision, bool]:
        """Apply one key. The boolean reports a decision was submitted.

        The latch is what makes a double keypress harmless: once submitted, every
        key is ignored until :meth:`open` or :meth:`clear` runs, so the next
        request cannot be answered by the tail of this one.
        """
        if self.request is None or self.submitted:
            return self, Decision(""), False
        lowered = key.lower()
        match lowered:
            case "up" | "k":
                if self.selection > 0:
                    self.selection -= 1
                return self, Decision(""), False
            case "down" | "j":
                if self.selection < 2:
                    self.selection += 1
                return self, Decision(""), False
            case "enter":
                decision = Decision(_DECISIONS_BY_ROW[self.selection])
            case "1" | "y":
                decision = APPROVE_ONCE
            case "2" | "a":
                decision = APPROVE_ALWAYS
            case "3" | "n":
                decision = DENY
            case _:
                return self, Decision(""), False
        self.submitted = True
        return self, decision, True

    def view(self, fallbackCWD: str) -> Text:
        """The menu, or an empty :class:`Text` when nothing is pending."""
        request = self.request
        if request is None:
            return Text()
        styles = self.styles
        rendered = Text()
        rendered.append(" ACTION REQUIRED ", style=styles.banner)
        if request.batch_total > 0:
            rendered.append(f" tool {request.batch_index}/{request.batch_total}:")
        rendered.append(" approve ")
        rendered.append(request.tool_name, style=_BOLD)
        rendered.append("?")
        for index, option in enumerate(("1. Yes, run once", "2. Yes, always allow", "3. No, deny")):
            prefix, style = "  ", styles.dim
            if index == self.selection:
                prefix, style = _SELECTED_MARKER + " ", styles.selected
            rendered.append("\n")
            rendered.append(prefix + option, style=style)
        if self.submitted:
            rendered.append("\n")
            rendered.append(" Decision submitted…", style=styles.accent)
        if request.command_class != "" or request.reason != "":
            cwd = request.cwd or fallbackCWD
            meta = f" class: {request.command_class} cwd: {cwd}"
            if request.touched_paths:
                meta += " paths: " + ",".join(request.touched_paths)
            if request.reason != "":
                meta += " reason: " + request.reason
            rendered.append("\n")
            rendered.append(meta, style=styles.dim)
        elif request.input != "":
            user_input = request.input
            if len(user_input) > 240:
                user_input = user_input[:240] + "..."
            rendered.append("\n")
            rendered.append(f" cwd: {fallbackCWD} input: {user_input}", style=styles.dim)
        return rendered
