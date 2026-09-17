"""The full-screen transcript pager: scrolling and searching retained output.

``docs/tui.md`` gives the ``pager`` key context two jobs and no others: scrolling
and searching the transcript. A caller opens this overlay with the transcript it
has already composed, as :class:`rich.text.Text`; the pager keeps no transcript
state and never renders one, so it imports no sibling feature (R6, R8) and no
part of the root package. It is a pure view: no ports, no I/O, no runtime.

The search is two pure functions rather than methods. What a search does is
arithmetic over a string, and a widget is a poor place to test arithmetic:
:func:`find_matches` returns the half-open character ranges of every occurrence,
and :func:`highlight_matches` paints them onto a copy. The screen keeps only the
cursor — the needle, the matches, and which of them is current.

Two keyboard rules come from the focus rule in ``docs/tui.md#key-contexts``.
The scroll pane cannot take focus, so the pager's own bindings answer the keys
rather than Textual's defaults for a scrollable widget; and while the search
prompt is open it owns the keyboard, so ``Esc`` leaves the prompt first and the
pager second.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import ClassVar

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Input, Static

__all__ = ["PagerScreen", "find_matches", "highlight_matches"]

#: One match: the half-open ``(start, end)`` character range of the haystack.
type MatchSpan = tuple[int, int]

#: How every match is painted: the accent role, which is the colour the interface
#: gives a selection. ``cyan`` is a terminal colour by name — which colour the
#: terminal draws for it is the terminal's decision, not this module's.
_MATCH_STYLE = Style(color="cyan", bold=True)

#: How the current match is painted. Reverse video names no second colour: the
#: terminal resolves the inversion against whatever it is already showing, so the
#: current match stands out from the other matches on any palette.
_CURRENT_MATCH_STYLE = Style(reverse=True, bold=True)

#: What the footer says before anything has been searched for. A pager is the one
#: surface whose keys are not visible, so it names them. The separators are U+00B7
#: (middle dot), written as escapes so the source stays readable as ASCII.
_IDLE_STATUS = "j/k scroll \u00b7 / search \u00b7 n/N match \u00b7 q close"


def find_matches(haystack: str, needle: str) -> tuple[MatchSpan, ...]:
    """Every non-overlapping occurrence of ``needle`` in ``haystack``.

    Matching is case-insensitive, and a span is a half-open ``(start, end)``
    character range of ``haystack`` as it was given. The search runs through
    :func:`re.finditer` over the original string rather than over a lower-cased
    copy, because case folding can change a character's length and every span is
    an index into the caller's own text.

    An empty needle matches nothing. It would otherwise match at every offset
    including the end, which is a zero-width match rather than a search, and is
    how a naive implementation walks off the end of the string.
    """
    if not needle:
        return ()
    return tuple((match.start(), match.end()) for match in re.finditer(re.escape(needle), haystack, re.IGNORECASE))


def highlight_matches(text: Text, spans: Sequence[MatchSpan], *, style: Style = _MATCH_STYLE) -> Text:
    """A copy of ``text`` with every span in ``spans`` painted ``style``.

    Copying is what keeps this pure: the caller's text is the transcript and is
    never written to. Spans are the ones :func:`find_matches` returned for this
    same text. They are expected to be disjoint, and disjoint is all the pager
    produces, so which one is painted last does not matter.
    """
    highlighted = text.copy()
    for start, end in spans:
        highlighted.stylize(style, start, end)
    return highlighted


class PagerScreen(ModalScreen[None]):
    """The full-screen transcript pager: scrolling and searching, nothing else.

    The content is opened at its first line, like a pager reading a file: the
    transcript viewport behind the overlay already shows the latest output, and
    ``G``/``end`` is one key away.

    ``n`` and ``N`` step from the current match and wrap around, rather than
    searching again from the viewport, so a run of ``n`` visits every match
    exactly once. A fresh search starts at the first match and brings it to the
    top of the viewport.
    """

    #: The pager takes focus itself: the scroll pane must not answer the page
    #: keys, and the search prompt is opened explicitly.
    AUTO_FOCUS: ClassVar[str | None] = ""

    DEFAULT_CSS = """
    PagerScreen { align: center middle; background: $background 70%; }
    PagerScreen > Vertical {
        width: 90%; height: 85%; border: round $accent; background: $surface; padding: 1 2;
    }
    #pager-title { height: 1; color: $text-muted; text-style: dim; }
    #pager-body { height: 1fr; scrollbar-gutter: stable; }
    #pager-body-text { width: 1fr; height: auto; }
    #pager-search { display: none; height: 1; border: none; padding: 0; }
    #pager-status { height: 1; color: $text-muted; text-style: dim; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "escape_or_close", "Close"),
        Binding("q", "close", "Close"),
        Binding("slash", "open_search", "Search"),
        Binding("n", "next_match", "Next match"),
        Binding("N", "previous_match", "Previous match"),
        Binding("j", "line_down", "Down"),
        Binding("k", "line_up", "Up"),
        Binding("down", "line_down", "Down", show=False),
        Binding("up", "line_up", "Up", show=False),
        Binding("g", "go_top", "Top"),
        Binding("G", "go_bottom", "Bottom"),
        Binding("home", "go_top", "Top", show=False),
        Binding("end", "go_bottom", "Bottom", show=False),
        Binding("pageup", "previous_page", "Page up", show=False),
        Binding("pagedown", "next_page", "Page down", show=False),
    ]

    def __init__(self, content: Text, *, title: str = "Transcript") -> None:
        super().__init__()
        self.content = content
        self.title = title
        #: The committed search, which is what the footer and ``n``/``N`` read.
        self.needle: str = ""
        self.matches: tuple[MatchSpan, ...] = ()
        self.match_index: int = -1

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(str(self.title), id="pager-title", markup=False)
            pane = VerticalScroll(id="pager-body")
            # The pager performs its own scrolling, so the pane must not take
            # focus: a focused scroll pane answers the page keys itself and the
            # pager's binding for them would never run.
            pane.can_focus = False
            with pane:
                yield Static(self._body(), id="pager-body-text", markup=False)
            yield Input(placeholder="Search", id="pager-search")
            yield Static(self.status(), id="pager-status", markup=False)

    @property
    def searching(self) -> bool:
        """Whether the search prompt currently owns the keyboard.

        Asked of the prompt itself rather than tracked beside it, so the state
        ``Esc`` and the footer read cannot drift from what is on screen.
        """
        return bool(self.query_one("#pager-search", Input).display)

    def status(self) -> Text:
        """The footer: the match count, why there is none, or the keys.

        A search that found nothing says so, so an empty result never looks the
        same as no search at all.
        """
        if not self.needle:
            return Text(_IDLE_STATUS)
        if not self.matches:
            return Text(f'no matches for "{self.needle}"')
        current = Text(f"{self.match_index + 1}/{len(self.matches)}", style=_MATCH_STYLE)
        current.append(f"  {self.needle}")
        return current

    def line_of(self, offset: int) -> int:
        """The zero-based line of the content holding character ``offset``.

        This is what turns a match into a place to scroll to. The mapping is by
        newline rather than by drawn row, so a match inside a line the pane wraps
        scrolls to the start of that line: still visible, not at the top.
        """
        return self.content.plain.count("\n", 0, offset)

    def action_close(self) -> None:
        """Leave the pager."""
        self.dismiss(None)

    def action_escape_or_close(self) -> None:
        """``Esc`` leaves the search prompt first and the pager second."""
        if self.searching:
            self.close_search()
        else:
            self.action_close()

    def action_open_search(self) -> None:
        """Open the prompt on the previous needle, so it can be re-run or replaced.

        Focusing the prompt selects what is in it, so typing starts a new search
        and ``Enter`` repeats the old one.
        """
        prompt = self.query_one("#pager-search", Input)
        prompt.value = self.needle
        prompt.display = True
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """``Enter`` commits the needle and hands the keyboard back to the pager."""
        event.stop()
        self.needle = event.value
        self.matches = find_matches(self.content.plain, self.needle)
        self.match_index = 0 if self.matches else -1
        self.close_search()
        self._refresh()
        self._scroll_to_match()

    def close_search(self) -> None:
        """Put the prompt away, discarding anything typed but not submitted."""
        prompt = self.query_one("#pager-search", Input)
        prompt.display = False
        prompt.value = self.needle
        # A hidden widget keeps focus until it is taken away, and a focused
        # hidden widget would swallow every key the pager binds.
        self.set_focus(None)

    def action_next_match(self) -> None:
        """Go to the next match, wrapping past the last."""
        self._step_match(1)

    def action_previous_match(self) -> None:
        """Go to the previous match, wrapping past the first."""
        self._step_match(-1)

    def action_line_down(self) -> None:
        self._pane().scroll_relative(y=1, animate=False)

    def action_line_up(self) -> None:
        self._pane().scroll_relative(y=-1, animate=False)

    def action_previous_page(self) -> None:
        self._pane().scroll_page_up(animate=False)

    def action_next_page(self) -> None:
        self._pane().scroll_page_down(animate=False)

    def action_go_top(self) -> None:
        self._pane().scroll_home(animate=False)

    def action_go_bottom(self) -> None:
        self._pane().scroll_end(animate=False)

    def _step_match(self, step: int) -> None:
        """Move ``step`` matches through the list, wrapping at either end."""
        count = len(self.matches)
        if not count:
            return
        self.match_index = (self.match_index + step) % count
        self._refresh()
        self._scroll_to_match()

    def _scroll_to_match(self) -> None:
        """Bring the current match to the top of the viewport."""
        if self.match_index < 0:
            return
        start, _ = self.matches[self.match_index]
        self._pane().scroll_to(y=self.line_of(start), animate=False)

    def _refresh(self) -> None:
        """Repaint the body and the footer from the search cursor."""
        self.query_one("#pager-body-text", Static).update(self._body())
        self.query_one("#pager-status", Static).update(self.status())

    def _body(self) -> Text:
        """The content with every match painted, the current one distinguishable."""
        if not self.matches:
            return self.content
        current = self.matches[self.match_index] if self.match_index >= 0 else None
        painted = highlight_matches(self.content, [span for span in self.matches if span != current])
        return painted if current is None else highlight_matches(painted, (current,), style=_CURRENT_MATCH_STYLE)

    def _pane(self) -> VerticalScroll:
        return self.query_one("#pager-body", VerticalScroll)
