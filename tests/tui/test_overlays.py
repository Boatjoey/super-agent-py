"""The overlays around the composer: the palette, help, approval, and output.

``docs/tui.md#layout`` splits the screen in two: the transcript is a viewport,
and approval, help, command choices, and long command output are overlays that
restore focus to their previous owner when they close. The behaviours pinned
here are the ones that split needs:

* the command palette floats above the composer and takes no height from the
  transcript, because it is an overlay rather than another row of the layout;
* the queue preview belongs to the composer area, so a queued prompt shows up
  there bounded to three rows and a summary;
* approval is a centred card rather than a pane that fills the screen;
* ``F1``, ``?``, and ``/help`` all raise the one help surface, and closing it
  gives the keyboard back — ``/help`` used to set the model's flag with nothing
  rendering it, which left the composer swallowing Enter until ``Esc``.

The conversation port is a fake, because these tests drive the real application:
the same double ``tests/tui/test_app.py`` uses, copied rather than imported so
this module stands on its own. Assertions read widget and app state — regions,
focus, display flags — rather than rendered strings, which a Rich measurement
would make terminal-dependent.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from rich.text import Text
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Static, TextArea

from super_agent.tui import (
    ROLE_ASSISTANT,
    AgentStatus,
    AgentSummary,
    App,
    Application,
    ApprovalDecision,
    AttachmentSummary,
    Cancellation,
    Channel,
    ConversationNotification,
    ConversationView,
    MCPServerSummary,
    Message,
    MessageAppended,
    PermissionRequest,
    SessionSummary,
    StartupInfo,
    ToolApprovalCleared,
    ToolApprovalRequested,
    ToolCall,
    new,
)
from super_agent.tui.application import HelpOverlay, OutputScreen
from super_agent.tui.approval import ApprovalDialog


class FakeConversation:
    """The ``Conversation`` port, scripted.

    It holds the scripted state: what ``run_turn`` notifies, what each read
    returns, and what the TUI asked it to do. ``run_turn`` closes the notification
    channel before returning, exactly as the runtime session does when the turn
    ends.
    """

    def __init__(
        self,
        *,
        script: Sequence[ConversationNotification] = (),
        custom_commands: dict[str, str] | None = None,
        extra_messages: int = 0,
        permission_error: BaseException | None = None,
        mcp_servers: Sequence[MCPServerSummary] = (),
        reject_snapshots: bool = False,
        run_error: BaseException | None = None,
    ) -> None:
        self.script = list(script)
        self.custom_command_map = custom_commands or {}
        self.extra_messages = extra_messages
        self.permission_error = permission_error
        self.mcp_servers = list(mcp_servers)
        self.reject_snapshots = reject_snapshots
        self.run_error = run_error
        self.queries: list[str] = []
        self.cancelled_at_start: list[bool] = []
        self.cancels = 0
        self.current_permission_mode = ""
        self.mcp_added: tuple[str, str, list[str]] | None = None
        self.attached_paths: list[str] = []
        self.pending: list[AttachmentSummary] = []

    # -- SnapshotPort and TurnPort -------------------------------------------

    def snapshot(self) -> ConversationView:
        assert not self.reject_snapshots, "unexpected Snapshot read"
        return ConversationView(agent_status=AgentStatus(label="Idle"))

    async def run_turn(
        self,
        text: str,
        notifications: Channel[ConversationNotification],
        approvals: Channel[ApprovalDecision],
        cancellation: Cancellation,
    ) -> BaseException | None:
        self.queries.append(text)
        self.cancelled_at_start.append(cancellation.cancelled)
        for notification in self.script:
            notifications.put(notification)
        for _ in range(self.extra_messages):
            notifications.put(MessageAppended(message=Message(role=ROLE_ASSISTANT, content="message " * 12)))
        notifications.close()
        return self.run_error

    async def cancel(self) -> BaseException | None:
        self.cancels += 1
        return None

    # -- SessionPort ----------------------------------------------------------

    async def reset(self) -> None:
        return None

    async def list_sessions(self) -> list[SessionSummary]:
        return []

    async def resume(self, session_id: str) -> None:
        return None

    async def rename_session(self, session_id: str, title: str) -> None:
        return None

    async def delete_session(self, session_id: str) -> None:
        return None

    async def compact(self, summary: str) -> None:
        return None

    async def undo(self) -> None:
        return None

    async def fork(self, title: str) -> str:
        return "fork"

    async def export(self, format: str) -> str:
        return "/tmp/export"

    # -- PermissionPort -------------------------------------------------------

    async def set_permission_mode(self, mode: str) -> None:
        self.current_permission_mode = mode
        if self.permission_error is not None:
            raise self.permission_error

    def permission_mode(self) -> str:
        return self.current_permission_mode

    def auto_approve_tools(self) -> bool:
        return self.current_permission_mode == "bypass"

    # -- MCPPort --------------------------------------------------------------

    def list_mcp_servers(self) -> list[MCPServerSummary]:
        return list(self.mcp_servers)

    async def add_mcp_server(self, name: str, command: str, args: list[str]) -> None:
        self.mcp_added = (name, command, list(args))

    async def remove_mcp_server(self, name: str) -> None:
        return None

    async def restart_mcp_server(self, name: str) -> None:
        return None

    # -- AgentPort ------------------------------------------------------------

    def list_agents(self) -> list[AgentSummary]:
        return []

    def current_agent(self) -> AgentSummary:
        return AgentSummary()

    async def use_agent(self, name: str) -> None:
        return None

    # -- MemoryPort -----------------------------------------------------------

    async def memories(self) -> list[str]:
        return []

    async def remember(self, text: str) -> None:
        return None

    async def forget_memories(self) -> None:
        return None

    # -- WorkspacePort --------------------------------------------------------

    async def git_diff(self) -> str:
        return "diff"

    async def git_status(self) -> str:
        return "status"

    async def diagnostics(self, path: str) -> str:
        return "[]"

    # -- ExtensionPort --------------------------------------------------------

    def custom_commands(self) -> list[str]:
        return list(self.custom_command_map)

    async def expand_custom_command(self, name: str, arguments: str) -> str:
        return self.custom_command_map[name].replace("$ARGUMENTS", arguments)

    def skills(self) -> list[str]:
        return []

    def plugins(self) -> list[str]:
        return []

    # -- attachments.Port -----------------------------------------------------

    async def attach(self, path: str) -> AttachmentSummary:
        self.attached_paths.append(path)
        return AttachmentSummary(name=path, mime="text/plain")

    async def pending_attachments(self) -> tuple[AttachmentSummary, ...]:
        return tuple(self.pending)


class ApprovalConversation(FakeConversation):
    """A turn that asks for approval, and ends once a decision arrives."""

    def __init__(self, *, clears: bool = True) -> None:
        super().__init__()
        self.decisions: list[ApprovalDecision] = []
        self.clears = clears

    async def run_turn(
        self,
        text: str,
        notifications: Channel[ConversationNotification],
        approvals: Channel[ApprovalDecision],
        cancellation: Cancellation,
    ) -> BaseException | None:
        self.queries.append(text)
        notifications.put(approval_request())
        decision = await approvals.get()
        if decision is not None:
            self.decisions.append(decision)
        if self.clears:
            notifications.put(ToolApprovalCleared())
            notifications.close()
        return None


class RunningConversation(FakeConversation):
    """A turn that stays in flight, so ``Tab`` queues instead of submitting."""

    async def run_turn(
        self,
        text: str,
        notifications: Channel[ConversationNotification],
        approvals: Channel[ApprovalDecision],
        cancellation: Cancellation,
    ) -> BaseException | None:
        self.queries.append(text)
        await cancellation.wait()
        return None


def approval_request(tool: str = "bash", command: str = "printf ok") -> ToolApprovalRequested:
    """The notification the runtime sends when a tool call needs a decision."""
    return ToolApprovalRequested(
        tool_call=ToolCall(name=tool, input=command),
        request=PermissionRequest(tool_name=tool, command_class="read-only", cwd="/repo", reason="risky"),
        batch_index=1,
        batch_total=1,
    )


def new_app(fake: FakeConversation, info: StartupInfo | None = None) -> App:
    """An app whose only dependencies are the fake and the given startup info."""
    return new(fake, info if info is not None else StartupInfo(model_name="test-model"))


def composer_text(program: Application) -> str:
    """The draft the composer widget is holding."""
    return program.query_one("#composer", TextArea).text


def composer_has_focus(program: Application) -> bool:
    """Whether the prompt editor owns the keyboard."""
    return program.query_one("#composer", TextArea).has_focus


def queue_preview(program: Application) -> Text:
    """The composer area's queued-prompt preview, as its widget holds it."""
    content = program.query_one("#queue", Static).content
    assert isinstance(content, Text), f"the preview is {content!r}"
    return content


async def open_approval_in(pilot: Pilot[None]) -> None:
    """Submit a prompt whose turn asks for approval, and wait for the card.

    ``Pilot`` is parameterised by the application's exit value, which this
    application does not have.
    """
    await pilot.press("g", "o", "enter")
    await pilot.pause()


# ---------------------------------------------------------------------------
# The command palette
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_palette_floats_above_the_composer_without_taking_transcript_height() -> None:
    """The palette is an overlay: the transcript keeps every row it had."""
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = program.query_one("#transcript", VerticalScroll)
        composer = program.query_one("#composer", TextArea)
        before = pane.region.height
        assert before > 0, "the transcript needs a viewport before it can be measured"

        await pilot.press("/")
        await pilot.pause()

        palette = program.query_one("#suggestions", Static)
        assert palette.display, "typing / opens the palette"
        assert palette.region.height > 0, "an open palette is drawn"
        assert palette.region.bottom <= composer.region.y, "the palette floats above the composer, not over it"
        assert pane.region.height == before, "an open palette must not take the transcript's height"


@pytest.mark.asyncio
async def test_compact_palette_floats_in_a_short_terminal() -> None:
    """The overlay holds at the size where the palette switches to its compact form."""
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(20, 8)) as pilot:
        await pilot.pause()
        pane = program.query_one("#transcript", VerticalScroll)
        before = pane.region.height

        await pilot.press("/")
        await pilot.pause()

        assert program.query_one("#suggestions", Static).display
        assert pane.region.height == before, "a short terminal is no different: the palette is an overlay"


@pytest.mark.asyncio
async def test_command_palette_keeps_the_composer_focused() -> None:
    """The palette is a suggestion, not a focus owner: the draft stays editable."""
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press("/")
        await pilot.pause()
        assert composer_has_focus(program), "the palette must not take the keyboard"

        await pilot.press("c", "l", "e", "a", "r")
        await pilot.pause()
        assert composer_text(program) == "/clear"
        assert composer_has_focus(program)

        await pilot.press("ctrl+u")
        await pilot.pause()
        assert program.query_one("#suggestions", Static).display is False, "no draft, no palette"
        assert composer_has_focus(program), "closing the palette leaves the composer where it was"


# ---------------------------------------------------------------------------
# The approval card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_dialog_is_a_centred_card() -> None:
    """A decision is a card, not a pane that fills the screen it covers."""
    fake = ApprovalConversation(clears=False)
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await open_approval_in(pilot)
        dialog = program.screen
        assert isinstance(dialog, ApprovalDialog), "the prompt is its own modal screen"
        card = dialog.query_one("#approval-dialog")

        screen = program.size
        assert card.container_size.height < screen.height, "the card must not fill the screen"
        assert card.container_size.width < screen.width, "the card must not span the terminal"

        centre_x, centre_y = card.region.center
        assert abs(centre_x - screen.width / 2) <= 1, f"the card is not centred horizontally: {card.region}"
        assert abs(centre_y - screen.height / 2) <= 1, f"the card is not centred vertically: {card.region}"

    assert fake.decisions == [], "showing a card answers nothing on its own"


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_command_raises_the_overlay_and_gives_enter_back() -> None:
    """``/help`` renders the one help surface, and closing it revives the composer.

    This is the regression: ``/help`` only set the model's flag, so the composer
    swallowed every key until ``Esc`` cleared a flag nothing had drawn.
    """
    fake = FakeConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/help", "enter")
        await pilot.pause()
        assert isinstance(program.screen, HelpOverlay), "a command's help request must raise the overlay"
        assert program.model.showHelp is True
        help_text = program.screen.query_one(Static).content
        assert "Commands & shortcuts" in str(help_text), "the overlay renders the help text"

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(program.screen, HelpOverlay), "closing help takes the overlay down"
        assert program.model.showHelp is False, "the flag follows the overlay's own dismissal"
        assert composer_has_focus(program), "the composer owns the keyboard again"

        await pilot.press("h", "i", "enter")
        await pilot.pause()

    assert fake.queries == ["hi"], "the composer answers Enter once help is closed"


@pytest.mark.asyncio
async def test_f1_and_the_help_key_open_the_one_help_surface() -> None:
    """``F1``, ``?``, and ``/help`` are three keys onto one overlay, not three overlays."""
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(program.screen, HelpOverlay), "F1 opens help"

        await pilot.press("f1")
        await pilot.pause()
        assert not isinstance(program.screen, HelpOverlay), "the key that opened help closes it"

        await pilot.press("?")
        await pilot.pause()
        assert isinstance(program.screen, HelpOverlay), "? opens help while the composer is empty"

        await pilot.press("escape")
        await pilot.pause()
        assert composer_has_focus(program)

        await pilot.press("w", "h", "y", "?")
        await pilot.pause()
        assert not isinstance(program.screen, HelpOverlay), "a draft keeps ? for itself"
        assert composer_text(program) == "why?"


@pytest.mark.asyncio
async def test_help_overlay_swallows_keys_meant_for_the_composer() -> None:
    """An open overlay owns the keyboard until it closes."""
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(program.screen, HelpOverlay)

        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()
        assert composer_text(program) == "", "typing must not reach the draft behind help"


# ---------------------------------------------------------------------------
# Focus returns to the composer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closing_the_approval_card_returns_focus_to_the_composer() -> None:
    fake = ApprovalConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await open_approval_in(pilot)
        assert isinstance(program.screen, ApprovalDialog)

        await pilot.press("y")
        await pilot.pause()
        assert not isinstance(program.screen, ApprovalDialog), "the runtime moved on"
        assert composer_has_focus(program), "the composer owns the keyboard again"

        await pilot.press("n", "e", "x", "t")
        await pilot.pause()
        assert composer_text(program) == "next"

    assert fake.decisions == [ApprovalDecision("once")]


@pytest.mark.asyncio
async def test_closing_command_output_returns_focus_to_the_composer() -> None:
    """Long command output is an overlay, and it hands the keyboard back."""
    info = StartupInfo(model_name="test-model", instruction_paths=("/repo/AGENTS.md",))
    program = Application(new_app(FakeConversation(), info))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/instructions", "enter")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(program.screen, OutputScreen), "command output opens the viewer"

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(program.screen, OutputScreen), "escape closes the viewer"
        assert composer_has_focus(program)

        await pilot.press("n", "e", "x", "t")
        await pilot.pause()
        assert composer_text(program) == "next"


# ---------------------------------------------------------------------------
# The queue preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_composer_area_previews_a_queued_prompt() -> None:
    """A queued follow-up is visible in the composer area until its turn starts."""
    fake = RunningConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press("a", "c", "t", "i", "v", "e", "enter")
        await pilot.pause()
        assert program.query_one("#queue", Static).display is False, "nothing is queued yet"

        await pilot.press("o", "n", "e", "tab")
        await pilot.pause()

        preview = queue_preview(program)
        assert program.query_one("#queue", Static).display is True
        assert "Queued (1)" in preview.plain, preview.plain
        assert "1. one" in preview.plain, preview.plain


@pytest.mark.asyncio
async def test_queue_preview_shows_three_and_summarises_the_rest() -> None:
    """``docs/tui.md`` bounds the preview: an area, not a growing list."""
    fake = RunningConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press("a", "c", "t", "i", "v", "e", "enter")
        await pilot.pause()
        for prompt in ("one", "two", "three", "four"):
            await pilot.press(*prompt, "tab")
            await pilot.pause()

        preview = queue_preview(program)
        for wanted in ("Queued (4)", "1. one", "2. two", "3. three", "… 1 more"):
            assert wanted in preview.plain, f"{wanted!r} missing from {preview.plain!r}"
        assert "4. four" not in preview.plain, "the preview shows at most three queued prompts"

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert program.query_one("#queue", Static).display is False, "a manual cancellation clears the queue"

    assert fake.queries == ["active"], "the cancelled turn is not followed by a queued one"
