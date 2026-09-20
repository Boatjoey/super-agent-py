"""The transcript pager: the search arithmetic, and the overlay that drives it.

Two layers, tested apart because they fail differently. ``find_matches`` and
``highlight_matches`` are pure functions, and what needs pinning there is the
arithmetic: several matches, none at all, adjacent ones, a needle longer than the
haystack, the empty needle that must not match forever, and the case folding that
moves a span off the text it indexes if the search is done on a lower-cased copy.
The screen is driven by a pilot, and its assertions are about what the pager
*does* — which match is current, where the viewport went, what the footer says,
who holds the keyboard — rather than about rendered strings, which a Rich
measurement would make terminal-dependent.

The probe mounts the overlay on a bare application carrying the project's theme,
the way ``test_theme.py`` mounts its own: the pager is the subject here, and the
application that opens it is another module's test.
"""

from __future__ import annotations

import pytest
from rich.style import Style
from rich.text import Text
from textual.app import App as TextualApp, ComposeResult
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Input, Static

from super_agent.tui import theme
from super_agent.tui.pager import PagerScreen, find_matches, highlight_matches

#: The ids the pager's stylesheet and its tests both name.
_TITLE, _BODY, _BODY_TEXT, _SEARCH, _STATUS = (
    "#pager-title",
    "#pager-body",
    "#pager-body-text",
    "#pager-search",
    "#pager-status",
)


class Probe(TextualApp[None]):
    """A terminal for the overlay to open on."""

    def __init__(self, transcript: Text, *, title: str = "Transcript") -> None:
        super().__init__()
        self.register_theme(theme.THEME)
        self.theme = theme.NAME
        self.transcript = transcript
        self.pager_title = title

    def on_mount(self) -> None:
        self.push_screen(PagerScreen(self.transcript, title=self.pager_title))


class BehindProbe(TextualApp[None]):
    """A probe with a widget behind the overlay, to see where the keyboard goes back to."""

    def __init__(self, transcript: Text) -> None:
        super().__init__()
        self.register_theme(theme.THEME)
        self.theme = theme.NAME
        self.transcript = transcript

    def compose(self) -> ComposeResult:
        yield Input(id="behind")

    def on_mount(self) -> None:
        self.query_one("#behind", Input).focus()
        self.push_screen(PagerScreen(self.transcript))


def transcript(lines: int = 120, *, needle: str = "needle", every: int = 10) -> Text:
    """A transcript of short lines, every ``every``-th of them holding the needle.

    Short lines matter: a match is scrolled to by line, so a line the pane wraps
    would put the assertion and the viewport on different numbers.
    """
    rendered = Text()
    for index in range(lines):
        if index:
            rendered.append("\n")
        rendered.append(f"line {index:03d}")
        if index % every == 0:
            rendered.append(f" {needle}")
    return rendered


async def open_pager(pilot: Pilot[None]) -> PagerScreen:
    """The overlay the probe pushed, mounted and waiting for keys."""
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, PagerScreen), f"the probe opens the pager, not {screen!r}"
    return screen


def pane(screen: PagerScreen) -> VerticalScroll:
    """The scroll pane the pager moves."""
    return screen.query_one(_BODY, VerticalScroll)


def footer(screen: PagerScreen) -> str:
    """What the footer widget is showing."""
    content = screen.query_one(_STATUS, Static).content
    assert isinstance(content, Text), f"the footer is {content!r}"
    return content.plain


def painted(screen: PagerScreen) -> Text:
    """What the body widget is showing."""
    content = screen.query_one(_BODY_TEXT, Static).content
    assert isinstance(content, Text), f"the body is {content!r}"
    return content


def painted_spans(text: Text) -> list[tuple[int, int, Style]]:
    """``text``'s spans as ``(start, end, style)``.

    Rich types a span's style as ``Style | str | None``, and every style this
    pager paints is a :class:`Style`, so narrowing here keeps the assertions
    about the role that reached the terminal.
    """
    return [(span.start, span.end, span.style) for span in text.spans if isinstance(span.style, Style)]


def prompt(screen: PagerScreen) -> Input:
    """The search prompt."""
    return screen.query_one(_SEARCH, Input)


async def search(pilot: Pilot[None], screen: PagerScreen, needle: str) -> None:
    """Type ``needle`` into the prompt, as a user would, and commit it."""
    await pilot.press("slash")
    await pilot.pause()
    for character in needle:
        await pilot.press(character)
    await pilot.press("enter")
    await pilot.pause()


# ---------------------------------------------------------------------------
# The search, as arithmetic
# ---------------------------------------------------------------------------


def test_find_matches_reports_every_occurrence_in_order() -> None:
    assert find_matches("one two one", "one") == ((0, 3), (8, 11))


def test_find_matches_is_case_insensitive() -> None:
    """The default a terminal pager is expected to have."""
    assert find_matches("Needle needle NEEDLE", "needle") == ((0, 6), (7, 13), (14, 20))
    assert find_matches("ünïcode", "ÜNÏCODE") == ((0, 7),)


@pytest.mark.parametrize(
    ("haystack", "needle"),
    [
        ("", "needle"),
        ("short", "a needle longer than the haystack"),
        ("nothing to see", "absent"),
    ],
)
def test_find_matches_finds_nothing_when_there_is_nothing(haystack: str, needle: str) -> None:
    assert find_matches(haystack, needle) == ()


def test_find_matches_does_not_overlap() -> None:
    """Two matches never share a character, which is what lets their spans differ in style."""
    assert find_matches("aaa", "aa") == ((0, 2),)
    assert find_matches("abab", "ab") == ((0, 2), (2, 4))
    assert find_matches("aaaa", "aa") == ((0, 2), (2, 4))


def test_find_matches_treats_an_empty_needle_as_no_search() -> None:
    """An empty pattern matches at every offset, so a naive search never finishes."""
    assert find_matches("anything", "") == ()
    assert find_matches("", "") == ()


def test_find_matches_reads_the_needle_literally() -> None:
    """A needle is text the user typed, not a pattern."""
    assert find_matches("abc abc", "a.c") == ()
    assert find_matches("a.c a.c", "a.c") == ((0, 3), (4, 7))
    assert find_matches("a+b", "a+b") == ((0, 3),)
    assert find_matches("aaa", "a|a") == ()


def test_find_matches_spans_index_the_text_that_was_given() -> None:
    """Case folding can change a character's length, and a span must still point at it.

    ``"İ".lower()`` is two characters, so searching a lower-cased copy reports
    offsets that have drifted past the text the spans are used to paint.
    """
    haystack = "İstanbul"

    assert find_matches(haystack, "stanbul") == ((1, 8),)
    for start, end in find_matches(haystack, "stanbul"):
        assert haystack[start:end] == "stanbul", "every span is a slice of the original text"


def test_highlight_matches_paints_every_span_without_touching_the_source() -> None:
    """The text handed in is the transcript, and the transcript is not ours to edit."""
    source = Text("one two one")
    chosen = Style(bold=True, reverse=True)
    painted = highlight_matches(source, find_matches(source.plain, "one"), style=chosen)

    assert [(start, end) for start, end, _style in painted_spans(painted)] == [(0, 3), (8, 11)]
    assert source.spans == [], "painting a copy leaves the caller's text alone"
    assert painted.plain == source.plain, "the search never changes the words"
    assert all(style == chosen for _start, _end, style in painted_spans(painted)), "the caller's style is the one used"


def test_highlight_matches_names_a_colour_for_a_match() -> None:
    """A match is a role, not the default foreground: it has to read as a highlight."""
    styles = [style for _start, _end, style in painted_spans(highlight_matches(Text("one"), ((0, 3),)))]

    assert styles, "the default style still paints"
    assert all(style.color is not None for style in styles), "a match is painted in a palette role"


# ---------------------------------------------------------------------------
# The overlay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pager_opens_on_the_content_with_the_keys_in_the_footer() -> None:
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)

        assert str(screen.query_one(_TITLE, Static).content) == "Transcript"
        assert painted(screen).plain == program.transcript.plain
        assert pane(screen).scroll_y == 0, "a pager opens at the top, like one reading a file"
        assert "search" in footer(screen) and "/" in footer(screen), footer(screen)
        assert pilot.app.focused is None, "nothing owns the keyboard, so the pager's bindings answer"


@pytest.mark.asyncio
async def test_j_and_k_scroll_one_line() -> None:
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)

        await pilot.press("j", "j", "j")
        await pilot.pause()
        assert pane(screen).scroll_y == 3

        await pilot.press("k")
        await pilot.pause()
        assert pane(screen).scroll_y == 2

        await pilot.press("k", "k", "k")
        await pilot.pause()
        assert pane(screen).scroll_y == 0, "scrolling up past the top stops at the top"


@pytest.mark.asyncio
async def test_page_keys_move_the_viewport_by_a_page() -> None:
    """The pane must not answer these itself: the pager's binding is the one that runs."""
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)

        await pilot.press("pagedown")
        await pilot.pause()
        after_page = pane(screen).scroll_y
        assert 1 < after_page < pane(screen).max_scroll_y, "a page is more than a line and less than the document"

        await pilot.press("pageup")
        await pilot.pause()
        assert pane(screen).scroll_y == 0


@pytest.mark.asyncio
async def test_g_and_shift_g_jump_to_the_ends() -> None:
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)

        await pilot.press("G")
        await pilot.pause()
        assert pane(screen).scroll_y == pane(screen).max_scroll_y
        assert pane(screen).is_vertical_scroll_end

        await pilot.press("g")
        await pilot.pause()
        assert pane(screen).scroll_y == 0


@pytest.mark.asyncio
async def test_home_and_end_are_the_same_jumps_as_g_and_shift_g() -> None:
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)

        await pilot.press("end")
        await pilot.pause()
        assert pane(screen).scroll_y == pane(screen).max_scroll_y

        await pilot.press("home")
        await pilot.pause()
        assert pane(screen).scroll_y == 0


@pytest.mark.asyncio
async def test_search_reports_the_match_count_and_the_current_match() -> None:
    program = Probe(transcript(lines=120, every=10))
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)

        await search(pilot, screen, "needle")

        assert screen.needle == "needle"
        assert len(screen.matches) == 12, "every tenth line of 120 carries the needle"
        assert screen.match_index == 0, "a search starts at its first match"
        assert footer(screen).startswith("1/12"), footer(screen)
        assert pane(screen).scroll_y == screen.line_of(screen.matches[0][0]), "the match is brought into view"


@pytest.mark.asyncio
async def test_a_search_that_finds_nothing_says_so() -> None:
    """An empty result must not look like no search at all."""
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        idle = footer(screen)

        await search(pilot, screen, "absent")

        assert screen.matches == ()
        assert screen.match_index == -1
        assert "no matches" in footer(screen), footer(screen)
        assert footer(screen) != idle, "a failed search is not the idle footer"
        assert painted(screen).spans == [], "nothing is highlighted"


@pytest.mark.asyncio
async def test_a_search_with_an_empty_prompt_goes_back_to_no_search() -> None:
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")
        assert painted(screen).spans

        # The prompt opens with the last search selected, so clearing it is what
        # starts a new one -- and an empty search is a search for nothing.
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("backspace")
        await pilot.press("enter")
        await pilot.pause()

        assert screen.needle == ""
        assert screen.matches == ()
        assert painted(screen).spans == [], "an empty search clears the highlight"


@pytest.mark.asyncio
async def test_n_and_shift_n_step_through_the_matches_and_wrap() -> None:
    program = Probe(transcript(lines=50, every=10))
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")
        assert len(screen.matches) == 5

        await pilot.press("n", "n")
        await pilot.pause()
        assert screen.match_index == 2
        assert footer(screen).startswith("3/5"), footer(screen)

        await pilot.press("N")
        await pilot.pause()
        assert screen.match_index == 1

        await pilot.press("N", "N")
        await pilot.pause()
        assert screen.match_index == 4, "stepping back past the first match wraps to the last"

        await pilot.press("n")
        await pilot.pause()
        assert screen.match_index == 0, "stepping past the last match wraps to the first"


@pytest.mark.asyncio
async def test_stepping_to_a_match_brings_it_to_the_viewport() -> None:
    program = Probe(transcript(lines=120, every=10))
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")

        await pilot.press("n", "n", "n")
        await pilot.pause()

        match = screen.matches[screen.match_index]
        assert pane(screen).scroll_y == screen.line_of(match[0]), "each step follows the match"


@pytest.mark.asyncio
async def test_the_body_paints_every_match_and_marks_the_current_one() -> None:
    """Two roles, so the match the pager would step to next is the visible one."""
    program = Probe(transcript(lines=50, every=10))
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")

        spans = painted_spans(painted(screen))
        assert sorted((start, end) for start, end, _style in spans) == list(screen.matches), "every match is painted"
        current = screen.matches[screen.match_index]
        marked = [style for start, end, style in spans if (start, end) == current]
        assert marked and marked[0].reverse, "the current match is the one marked out"
        others = [style for start, end, style in spans if (start, end) != current]
        assert others, "the other matches are painted too"
        assert not any(style.reverse for style in others), "and they are not marked as current"


@pytest.mark.asyncio
async def test_the_search_never_writes_to_the_transcript_it_was_given() -> None:
    program = Probe(transcript())
    before = program.transcript.plain
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")
        await pilot.press("n", "N")
        await pilot.pause()

        assert program.transcript.plain == before
        assert program.transcript.spans == [], "the caller's text is not the pager's to paint"
        assert painted(screen).plain == before, "the pager paints a copy"


@pytest.mark.asyncio
async def test_an_empty_transcript_opens_without_incident() -> None:
    """``Ctrl+T`` before anything has been said must not be a crash."""
    program = Probe(Text(""))
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)

        await pilot.press("j", "pagedown", "G", "n", "slash", "x", "enter")
        await pilot.pause()

        assert screen.matches == ()
        assert "no matches" in footer(screen)
        assert pane(screen).scroll_y == 0


@pytest.mark.asyncio
async def test_n_without_a_search_does_nothing() -> None:
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)

        await pilot.press("n", "N")
        await pilot.pause()

        assert screen.match_index == -1
        assert pane(screen).scroll_y == 0, "there is nothing to step to"


# ---------------------------------------------------------------------------
# The keyboard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slash_opens_the_prompt_on_the_previous_search() -> None:
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")

        await pilot.press("slash")
        await pilot.pause()

        assert screen.searching
        assert prompt(screen).value == "needle", "the last search comes back to be edited or re-run"
        assert pilot.app.focused is prompt(screen)


@pytest.mark.asyncio
async def test_typing_in_the_prompt_does_not_reach_the_pager_bindings() -> None:
    """``q``, ``n``, and ``g`` are text while the prompt owns the keyboard."""
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")
        before = pane(screen).scroll_y

        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("q", "n", "N", "g", "G", "slash")
        await pilot.pause()

        assert prompt(screen).value == "qnNgG/", "the keys are text, not bindings"
        assert screen.searching, "q must type a q, not close the pager"
        assert screen.needle == "needle", "nothing is committed until Enter"
        assert screen.match_index == 0, "n must type an n, not step the matches"
        assert pane(screen).scroll_y == before, "the pager stays where it was"


@pytest.mark.asyncio
async def test_escape_closes_the_prompt_first_and_the_pager_second() -> None:
    """The key leaves whatever owns focus, which is the rule ``docs/tui.md`` gives."""
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")
        await pilot.press("slash")
        await pilot.pause()

        await pilot.press("escape")
        await pilot.pause()
        assert not screen.searching, "escape leaves the prompt"
        assert isinstance(pilot.app.screen, PagerScreen), "and the pager stays open"

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(pilot.app.screen, PagerScreen), "the next escape leaves the pager"


@pytest.mark.asyncio
async def test_the_pager_answers_keys_again_once_the_prompt_is_closed() -> None:
    """Closing the prompt gives the keyboard back rather than leaving it hidden and focused."""
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        await pilot.press("j")
        await pilot.pause()
        assert pane(screen).scroll_y == 1, "j scrolls again"

        await pilot.press("n")
        await pilot.pause()
        assert screen.match_index == 1, "n steps the matches again"


@pytest.mark.asyncio
async def test_escape_in_the_prompt_keeps_the_search_it_abandoned() -> None:
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)
        await search(pilot, screen, "needle")
        matches = screen.matches

        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("z", "z", "z")
        await pilot.press("escape")
        await pilot.pause()

        assert screen.needle == "needle", "the abandoned edit is discarded"
        assert screen.matches == matches
        assert "no matches" not in footer(screen)


@pytest.mark.asyncio
async def test_q_closes_the_pager() -> None:
    program = Probe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        await open_pager(pilot)

        await pilot.press("q")
        await pilot.pause()

        assert not isinstance(pilot.app.screen, PagerScreen), "q leaves the pager"


@pytest.mark.asyncio
async def test_closing_the_pager_gives_the_keyboard_back() -> None:
    """``docs/tui.md#layout``: an overlay restores focus to its previous owner."""
    program = BehindProbe(transcript())
    async with program.run_test(size=(70, 16)) as pilot:
        await open_pager(pilot)
        await pilot.press("q")
        await pilot.pause()

        assert not isinstance(pilot.app.screen, PagerScreen)
        behind = pilot.app.screen.query_one("#behind", Input)
        assert behind.has_focus, "the widget behind the overlay owns the keyboard again"

        await pilot.press("h", "i")
        await pilot.pause()
        assert behind.value == "hi", "and it receives what is typed"


@pytest.mark.asyncio
async def test_the_pager_shows_the_title_it_was_given() -> None:
    program = Probe(transcript(), title="Conversation")
    async with program.run_test(size=(70, 16)) as pilot:
        screen = await open_pager(pilot)

        assert str(screen.query_one(_TITLE, Static).content) == "Conversation"
