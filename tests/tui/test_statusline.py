"""The status line: an ordered row of items, composed from the model.

``docs/tui.md#layout`` gives the root this row and ``docs/config.md#tui`` gives it
the vocabulary. The composition is pure — no widget, no port, no terminal — so
these tests call it directly with an app built around an unused conversation
port and assert the row it produces.

The behaviours under test: every item in isolation, each item's data being
unavailable, the default order, a configured order, ``null`` removing the row,
and the roles the row's colours come from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Never, cast

import pytest
from rich.style import Style
from rich.text import Text

from super_agent.tui import App, ContextUsage, StartupInfo, new
from super_agent.tui.conversation import AgentStatus, Conversation
from super_agent.tui.statusline import DEFAULT_ORDER, ITEMS, SEPARATOR, compose, order


class _UnusedPort:
    """The conversation port, unused: composing the status line reads no port.

    ``new`` asks the extension port for its custom commands while it wires the
    command catalogue, so that one method answers. Anything else being reached is
    the status line calling a port, which is the bug these tests exist to prevent.
    """

    def custom_commands(self) -> list[str]:
        return []

    def __getattr__(self, name: str) -> Never:
        raise AssertionError("the status line must not call " + name)


def new_app(info: StartupInfo) -> App:
    """An app whose only meaningful input is the startup info."""
    return new(cast("Conversation", _UnusedPort()), info)


def row(app: App) -> str:
    """The plain text of the composed row."""
    return compose(app).plain


def style_at(text: Text, offset: int) -> Style | None:
    """The style covering ``offset``, or ``None`` when no span paints it."""
    for span in text.spans:
        if span.start <= offset < span.end and isinstance(span.style, Style):
            return span.style
    return None


# ---------------------------------------------------------------------------
# The vocabulary and its order
# ---------------------------------------------------------------------------


def test_items_are_the_documented_vocabulary() -> None:
    assert ITEMS == ("model", "approval", "context_usage", "session_id", "sandbox", "cwd", "spinner")


def test_default_order_is_the_documented_order() -> None:
    assert DEFAULT_ORDER == ("model", "approval", "context_usage")


def test_absent_setting_draws_the_default_order() -> None:
    assert order(None) == DEFAULT_ORDER


def test_null_setting_draws_no_row() -> None:
    app = new_app(StartupInfo(model_name="m", permission_mode="ask", status_line=()))

    assert row(app) == ""
    assert order(()) == ()


def test_configured_order_is_drawn_left_to_right() -> None:
    app = new_app(
        StartupInfo(
            model_name="test-model",
            permission_mode="ask",
            cwd="/repo",
            status_line=("cwd", "model"),
        )
    )

    assert row(app) == "/repo" + SEPARATOR + "test-model"


def test_every_item_is_rendered() -> None:
    """A name in :data:`ITEMS` with no renderer would raise on the row, not draw."""
    app = new_app(
        StartupInfo(
            model_name="test-model",
            permission_mode="ask",
            cwd="/repo",
            session_id="20260917T120000123456789",
            sandbox="strict",
            status_line=ITEMS,
        )
    )
    app.contextUsage = ContextUsage(input_tokens=1200, output_tokens=340, total_tokens=1540)
    app.agentStatus = AgentStatus(label="Idle")

    drawn = row(app).split(SEPARATOR)

    assert len(drawn) == len(ITEMS), f"every item must draw something: {drawn}"
    assert drawn[0] == "test-model"
    assert drawn[1] == "ask"
    assert drawn[2] == "↑1.2k ↓340"
    assert drawn[3] == "20260917T120000123456789"
    assert drawn[4] == "strict"
    assert drawn[5] == "/repo"
    assert drawn[6] == "Idle"


# ---------------------------------------------------------------------------
# Each item on its own
# ---------------------------------------------------------------------------


def test_model_item_shows_the_model_name() -> None:
    app = new_app(StartupInfo(model_name="test-model", status_line=("model",)))

    assert row(app) == "test-model"


def test_approval_item_shows_the_permission_mode() -> None:
    app = new_app(StartupInfo(permission_mode="accept-edits", status_line=("approval",)))

    assert row(app) == "accept-edits"


def test_context_usage_item_shows_absolute_token_counts() -> None:
    app = new_app(StartupInfo(status_line=("context_usage",)))
    app.contextUsage = ContextUsage(input_tokens=12, output_tokens=3, total_tokens=15)

    assert row(app) == "↑12 ↓3"
    assert "%" not in row(app), "the runtime carries no context-window size to divide by"


def test_context_usage_item_abbreviates_large_counts() -> None:
    app = new_app(StartupInfo(status_line=("context_usage",)))
    app.contextUsage = ContextUsage(input_tokens=128_000, output_tokens=1_500_000, total_tokens=1_628_000)

    assert row(app) == "↑128k ↓1.5M"


def test_session_id_item_shows_the_id() -> None:
    app = new_app(StartupInfo(session_id="20260917T120000123456789", status_line=("session_id",)))

    assert row(app) == "20260917T120000123456789"


def test_sandbox_item_shows_the_mode() -> None:
    app = new_app(StartupInfo(sandbox="strict", status_line=("sandbox",)))

    assert row(app) == "strict"


def test_cwd_item_abbreviates_the_home_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    app = new_app(StartupInfo(cwd=str(tmp_path / "repo"), status_line=("cwd",)))

    assert row(app) == "~/repo"


def test_spinner_item_shows_the_agent_label() -> None:
    app = new_app(StartupInfo(status_line=("spinner",)))
    app.agentStatus = AgentStatus(label="RunningTool", busy=True)

    assert row(app) == "RunningTool"


# ---------------------------------------------------------------------------
# Unavailable data omits the item
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item", ["model", "approval", "cwd", "session_id", "sandbox"])
def test_item_without_data_is_omitted(item: str) -> None:
    """Nothing to say is not an empty item: the row simply lacks it."""
    app = new_app(StartupInfo(status_line=(item,)))

    assert row(app) == ""


def test_context_usage_is_omitted_until_a_provider_reports_one() -> None:
    app = new_app(StartupInfo(status_line=("context_usage",)))

    assert app.contextUsage is None
    assert row(app) == ""


def test_a_zero_usage_report_is_not_a_report() -> None:
    app = new_app(StartupInfo(status_line=("context_usage",)))
    app.contextUsage = ContextUsage()

    assert row(app) == ""


def test_spinner_without_a_label_is_omitted() -> None:
    app = new_app(StartupInfo(status_line=("spinner",)))
    app.agentStatus = AgentStatus(label="")

    assert row(app) == ""


def test_an_omitted_item_costs_no_separator() -> None:
    """The row is the items that have data, not the items that were configured."""
    app = new_app(StartupInfo(model_name="test-model", permission_mode="", status_line=("model", "approval")))

    assert row(app) == "test-model"


def test_an_empty_row_is_empty() -> None:
    app = new_app(StartupInfo(status_line=("model", "approval")))

    assert row(app) == ""


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


def test_separator_is_secondary_and_items_carry_their_own_role() -> None:
    app = new_app(StartupInfo(model_name="test-model", permission_mode="ask", status_line=("model", "approval")))
    rendered = compose(app)

    assert style_at(rendered, 0) == app.styles.secondary
    separator_at = len("test-model")
    separator = rendered.plain[separator_at : separator_at + len(SEPARATOR)]
    assert separator == SEPARATOR
    assert style_at(rendered, separator_at) == app.styles.secondary


@pytest.mark.parametrize(
    ("status", "role"),
    [
        (AgentStatus(label="Idle"), "secondary"),
        (AgentStatus(label="WaitingLLM", busy=True), "accent"),
        (AgentStatus(label="WaitingApproval", awaiting_approval=True), "accent_bold"),
    ],
)
def test_spinner_role_follows_what_the_agent_is_doing(status: AgentStatus, role: str) -> None:
    app = new_app(StartupInfo(status_line=("spinner",)))
    app.agentStatus = status

    assert style_at(compose(app), 0) == getattr(app.styles, role)
