"""The TUI's visual vocabulary.

Rich supplies the two pieces this needs, so the module keeps a clean split:

* :class:`Styles` carries one :class:`rich.style.Style` per role, using Rich's
  ``color(N)`` form of the 256-colour palette.
* :class:`MarkdownRenderer` renders markdown for the transcript. Its wrapping and
  block padding differ from a width-perfect string, which is why the tests assert
  width invariants rather than rendered strings.
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

__all__ = ["DefaultMarkdownRenderer", "MarkdownRenderer", "Styles", "default_styles"]


class MarkdownRenderer(Protocol):
    """Markdown in, styled terminal text out."""

    def render(self, content: str, width: int) -> Text:
        """Render ``content`` for a terminal ``width`` columns wide."""
        ...


class DefaultMarkdownRenderer:
    """A markdown renderer built on :class:`rich.markdown.Markdown`.

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
    """Drop the blank padding and trailing spaces."""
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

    status: Style
    user_label: Style
    tool_label: Style
    command_label: Style
    thinking: Style
    error: Style
    footer: Style
    markdown_renderer: MarkdownRenderer


def default_styles() -> Styles:
    """The default styles: cyan accents, dim secondary text."""
    secondary, accent = "color(8)", "color(6)"
    return Styles(
        status=Style(color=accent, italic=True),
        user_label=Style(color="color(2)", bold=True),
        tool_label=Style(color=accent, bold=True),
        command_label=Style(color="color(3)", bold=True),
        thinking=Style(color=secondary, italic=True),
        error=Style(color="color(1)", bold=True),
        footer=Style(color=secondary, italic=True),
        markdown_renderer=DefaultMarkdownRenderer(),
    )
