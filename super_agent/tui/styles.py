"""The TUI's visual vocabulary.

Ported from the Go ``tui/styles.go``. Go builds lipgloss styles and one glamour
``TermRenderer``; Rich has the same two pieces, so the port keeps the same split:

* :class:`Styles` carries one :class:`rich.style.Style` per role. lipgloss colour
  numbers are Rich's ``color(N)`` form of the same 256-colour palette, so
  ``lipgloss.Color("6")`` becomes ``color(6)``.
* :class:`MarkdownRenderer` stands in for glamour's ``TermRenderer``. The two
  disagree about wrapping and about how much padding a block gets, which is why
  the tests assert width invariants rather than rendered strings.
"""

from __future__ import annotations

import dataclasses
import io
from typing import Protocol

from rich.console import Console
from rich.markdown import Markdown
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

__all__ = ["DefaultMarkdownRenderer", "DefaultStyles", "MarkdownRenderer", "Styles"]


class MarkdownRenderer(Protocol):
    """Markdown in, styled terminal text out."""

    def render(self, content: str, width: int) -> Text:
        """Render ``content`` for a terminal ``width`` columns wide."""
        ...


class DefaultMarkdownRenderer:
    """glamour's ``TermRenderer``, built on :class:`rich.markdown.Markdown`.

    Rendering happens through a private console whose width is the caller's, so
    the result is a plain :class:`rich.text.Text` the transcript can compose,
    clamp, and measure without a console of its own.
    """

    __slots__ = ()

    def render(self, content: str, width: int) -> Text:
        console = Console(
            width=max(1, width),
            file=io.StringIO(),
            force_terminal=False,
            color_system=None,
            legacy_windows=False,
        )
        lines = console.render_lines(Markdown(content), console.options, pad=False)
        rendered = Text()
        for index, segments in enumerate(lines):
            if index:
                rendered.append("\n")
            for segment in segments:
                rendered.append(segment.text, style=_style_of(segment))
        return _strip_blank_lines(rendered)


def _style_of(segment: Segment) -> Style | None:
    """The segment's style, which Rich types as ``Style | str | None``."""
    style = segment.style
    return style if isinstance(style, Style) else None


def _strip_blank_lines(text: Text) -> Text:
    """Drop the blank padding glamour also trims, and trailing spaces with it."""
    lines: list[Text] = []
    for line in text.split("\n", allow_blank=True):
        line.rstrip()
        lines.append(line)
    start, end = 0, len(lines)
    while start < end and not lines[start]:
        start += 1
    while end > start and not lines[end - 1]:
        end -= 1
    rendered = Text()
    for index, line in enumerate(lines[start:end]):
        if index:
            rendered.append("\n")
        rendered.append_text(line)
    return rendered


@dataclasses.dataclass(frozen=True, slots=True)
class Styles:
    """One style per role, plus the markdown renderer the transcript uses."""

    Status: Style
    UserLabel: Style
    ToolLabel: Style
    CommandLabel: Style
    Thinking: Style
    Error: Style
    Footer: Style
    MarkdownRenderer: MarkdownRenderer


def DefaultStyles() -> Styles:
    """The Go ``DefaultStyles``: cyan accents, dim secondary text."""
    secondary, accent = "color(8)", "color(6)"
    return Styles(
        Status=Style(color=accent, italic=True),
        UserLabel=Style(color="color(2)", bold=True),
        ToolLabel=Style(color=accent, bold=True),
        CommandLabel=Style(color="color(3)", bold=True),
        Thinking=Style(color=secondary, italic=True),
        Error=Style(color="color(1)", bold=True),
        Footer=Style(color=secondary, italic=True),
        MarkdownRenderer=DefaultMarkdownRenderer(),
    )
