"""How one transcript message is composed.

A message is rendered as fragments -- a reasoning line, an interruption notice, a
body -- and the tests here assert on the fragments rather than on the joined
string, because the defects they cover were about *which style reached which
text*, not about the words.

The styles come from the root palette, mirroring the injection the composition
boundary performs, so an assertion about a role is an assertion about the colour
the terminal is actually handed.
"""

from __future__ import annotations

import pytest
from rich.style import Style
from rich.text import Text

from super_agent.tui import styles as palette, transcript
from super_agent.tui.transcript import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER, Message

#: The markers the transcript draws: the prompt U+276F, the reply and tool bullet
#: U+25CF. Escaped, so the assertions do not read as ambiguous text.
_PROMPT, _BULLET = "\u276f", "\u25cf"


def transcript_styles() -> transcript.Styles:
    """The transcript's styles as the composition boundary wires them."""
    root = palette.default_styles()
    return transcript.Styles(
        default=root.default,
        secondary=root.secondary,
        accent=root.accent,
        accent_bold=root.accent_bold,
        identity=root.identity,
        success=root.success,
        error=root.error,
        markdown_renderer=root.markdown_renderer,
    )


def model() -> transcript.Model:
    """A transcript model carrying the real palette."""
    return transcript.new("", transcript_styles())


def styles_at(text: Text, needle: str) -> list[Style]:
    """Every span style covering ``needle``, in the order they appear."""
    start = text.plain.index(needle)
    return [span.style for span in text.spans if span.start <= start < span.end and isinstance(span.style, Style)]


def test_reasoning_line_does_not_colour_the_prose_below_it() -> None:
    """A styled container used to bleed the reasoning role into the whole message.

    ``Text("Thinking...", style=...)`` followed by ``append_text`` applies its
    base style to everything appended, which rendered every assistant message dim
    and italic -- prose and tool summaries included.
    """
    message = Message(role=ROLE_ASSISTANT, content="plain prose", reasoning_content="why")
    rendered = model().renderCommitted(message, False, False)

    assert "plain prose" in rendered.plain, "the prose survives"
    assert not rendered.style, "the joined message carries no base style"
    assert styles_at(rendered, "plain prose") == [], "the prose is painted by no role of its own"
    assert all(style.dim for style in styles_at(rendered, "Thinking...")), "the reasoning line is the dim one"


def test_the_agent_marker_colours_only_the_marker() -> None:
    """A base style on the container would paint the whole reply in the marker's colour.

    Rich merges a ``Text``'s own style into every fragment appended to it, so a
    marker has to be appended with a style rather than set as the container's.
    """
    rendered = model().renderMessage(Message(role=ROLE_ASSISTANT, content="plain prose"), False)

    assert [style.color for style in styles_at(rendered, _BULLET)] == [palette.default_styles().identity.color]
    assert styles_at(rendered, "plain prose") == [], "the prose keeps the default foreground"


def test_a_message_without_reasoning_shows_no_thinking_line() -> None:
    """``Thinking...`` is the reasoning placeholder, not a decoration on every reply."""
    rendered = model().renderCommitted(Message(role=ROLE_ASSISTANT, content="just prose"), False, False)

    assert "just prose" in rendered.plain
    assert "Thinking..." not in rendered.plain


def test_the_agent_marker_sits_on_the_first_line_only() -> None:
    """Assistant prose wraps to the full width rather than inheriting an indent."""
    m = model()
    m.set_width(24)
    rendered = m.renderMessage(
        Message(role=ROLE_ASSISTANT, content="alpha beta gamma delta epsilon zeta eta theta"), False
    )

    lines = rendered.plain.split("\n")
    assert lines[0].startswith(_BULLET + " "), "the reply is marked"
    assert len(lines) > 1, "the prose wrapped at the width it was given"
    assert not lines[1].startswith(" "), "the wrapped line is not pushed in by the marker"


def test_the_user_prompt_keeps_its_own_marker_and_indent() -> None:
    """The prompt is prominent, and its marker is not the agent's."""
    rendered = model().renderMessage(Message(role=ROLE_USER, content="do it"), False)

    assert rendered.plain == f" {_PROMPT} do it"
    assert all(style.color is not None for style in styles_at(rendered, _PROMPT)), (
        "the prompt marker is an accent, not default text"
    )


def test_a_tool_result_renders_folded_and_counts_what_it_hid() -> None:
    """A result used to render as nothing at all: a role no branch handled."""
    message = Message(role=ROLE_TOOL, content="first line\nsecond\nthird")
    folded = model().renderMessage(message, False)

    assert "first line" in folded.plain
    assert "second" not in folded.plain, "the folded form keeps one line"
    assert "2 more lines" in folded.plain, "and says how much it kept back"


def test_a_tool_result_expands_to_its_whole_body() -> None:
    """Expanding shows every line rather than a longer fold."""
    message = Message(role=ROLE_TOOL, content="first line\nsecond\nthird")
    expanded = model().renderMessage(message, True)

    assert "first line" in expanded.plain
    assert "second" in expanded.plain and "third" in expanded.plain
    assert "more line" not in expanded.plain


def test_one_hidden_line_is_counted_in_the_singular() -> None:
    """The count reads as English."""
    folded = model().renderMessage(Message(role=ROLE_TOOL, content="shown\nhidden"), False)

    assert "1 more line" in folded.plain
    assert "1 more lines" not in folded.plain


def test_added_and_removed_lines_are_coloured() -> None:
    """A diff is the one place a result says more than its text does."""
    expanded = model().renderMessage(Message(role=ROLE_TOOL, content="+added\n-removed\ncontext"), True)

    assert [style.color for style in styles_at(expanded, "+added")] == [palette.default_styles().success.color]
    assert [style.color for style in styles_at(expanded, "-removed")] == [palette.default_styles().error.color]


def test_an_interrupted_message_says_so() -> None:
    """An interrupted turn with no body used to render as a permanent ``Thinking...``."""
    rendered = model().renderCommitted(Message(role=ROLE_ASSISTANT, interrupted=True), False, False)

    assert "Interrupted" in rendered.plain
    assert "Thinking..." not in rendered.plain


def test_an_interrupted_message_keeps_what_it_managed_to_say() -> None:
    """The notice follows the partial body rather than replacing it."""
    message = Message(role=ROLE_ASSISTANT, content="half a thought", interrupted=True)
    rendered = model().renderCommitted(message, False, False)

    assert "half a thought" in rendered.plain
    assert "Interrupted" in rendered.plain
    assert rendered.plain.index("half a thought") < rendered.plain.index("Interrupted")


def test_an_empty_message_contributes_no_blocks_at_all() -> None:
    """A message with nothing to show draws nothing, rather than a blank line."""
    assert model().blocks(Message(role=ROLE_ASSISTANT), False, False) == ()


@pytest.mark.parametrize("role", [ROLE_ASSISTANT, ROLE_TOOL])
def test_a_blank_content_message_is_never_rendered_as_a_stray_blank(role: transcript.Role) -> None:
    """Whitespace-only bodies do not add an empty block to the transcript."""
    assert model().blocks(Message(role=role, content="   \n  "), False, False) == ()
