"""The TUI's visual vocabulary.

Every colour the interface draws is named here, once, from the terminal's own
ANSI palette: ``docs/tui.md#appearance`` fixes six roles and this module is their
only home. A feature never constructs a colour — it declares the shape of the
styles it renders with and the root hands it the values, because a feature may
not import this module (R6). ``tests/architecture/test_theme.py`` fails when a
feature reaches for a colour anyway.

Rich supplies the two pieces this needs, so the module keeps a clean split:

* :class:`Styles` carries one :class:`rich.style.Style` per role.
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
from rich.theme import Theme

__all__ = ["DEFAULT_SYNTAX_THEME", "DefaultMarkdownRenderer", "MarkdownRenderer", "Styles", "default_styles"]

#: The ANSI colour names the palette uses, in Rich's spelling. Which colour each
#: one draws is the terminal's decision, not this module's (see
#: :mod:`super_agent.tui.theme` for the same roles stated for Textual).
_DEFAULT = "default"
_ACCENT = "cyan"
_SUCCESS = "green"
_FAILURE = "red"
_IDENTITY = "magenta"

#: The syntax theme fenced code is highlighted with unless configured otherwise.
#: Rich ships ANSI-only themes, so code follows the terminal palette instead of
#: carrying one of its own; ``monokai``, the Rich default, is truecolor and would
#: look wrong on a light terminal.
DEFAULT_SYNTAX_THEME = "ansi_dark"

#: Rich's own markdown styles paint inline code and code blocks on black, which
#: assumes a dark terminal. Both keep the colour and drop the background, so the
#: text sits on whatever the terminal is already showing.
_MARKDOWN_THEME = Theme(
    {
        "markdown.code": f"bold {_ACCENT}",
        "markdown.code_block": _ACCENT,
    }
)


class MarkdownRenderer(Protocol):
    """Markdown in, styled terminal text out."""

    def render(self, content: str, width: int) -> Text:
        """Render ``content`` for a terminal ``width`` columns wide."""
        ...


class DefaultMarkdownRenderer:
    """A markdown renderer built on :class:`rich.markdown.Markdown`.

    Rendering happens through a private console whose width is the caller's, so
    the result is a plain :class:`rich.text.Text` the transcript can compose,
    clamp, and measure without a console of its own. The console carries the
    palette's markdown theme, so a code block is highlighted in the terminal's
    colours rather than in a theme of Rich's choosing.
    """

    __slots__ = ("_syntax_theme",)

    def __init__(self, syntax_theme: str = DEFAULT_SYNTAX_THEME) -> None:
        self._syntax_theme = syntax_theme

    def render(self, content: str, width: int) -> Text:
        console = Console(
            width=max(1, width),
            file=io.StringIO(),
            force_terminal=False,
            color_system=None,
            legacy_windows=False,
            theme=_MARKDOWN_THEME,
        )
        markdown = Markdown(content, code_theme=self._syntax_theme, inline_code_theme=self._syntax_theme)
        lines = console.render_lines(markdown, console.options, pad=False)
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

    #: Default text, assistant prose, and tool output.
    default: Style
    #: Secondary text: reasoning, metadata, hints, and tree guides.
    secondary: Style
    #: User input, selection, and status indicators.
    accent: Style
    #: The accent for a marker the eye should catch: a prompt, a selected row.
    accent_bold: Style
    #: The accent as a block, for the approval banner.
    banner: Style
    #: Success and added lines.
    success: Style
    #: Errors, failures, and removed lines.
    error: Style
    #: The agent's identity marker.
    identity: Style
    markdown_renderer: MarkdownRenderer


def default_styles(syntax_theme: str = DEFAULT_SYNTAX_THEME) -> Styles:
    """The palette: the six roles of ``docs/tui.md#appearance`` and the emphasis they carry.

    ``syntax_theme`` names the theme fenced code is highlighted with; it is the
    one place a colour outside the six roles is allowed, because a syntax theme
    is a palette in its own right.
    """
    return Styles(
        default=Style(),
        secondary=Style(dim=True),
        accent=Style(color=_ACCENT),
        accent_bold=Style(color=_ACCENT, bold=True),
        banner=Style(color=_DEFAULT, bgcolor=_ACCENT, bold=True),
        success=Style(color=_SUCCESS),
        error=Style(color=_FAILURE, bold=True),
        identity=Style(color=_IDENTITY),
        markdown_renderer=DefaultMarkdownRenderer(syntax_theme),
    )
