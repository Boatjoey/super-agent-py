"""The TUI's routing, turn lifecycle, and user-visible interaction.

The tests drive the real Textual application; the TUI may not import ``runtime``
(R1), so the double defined here is a fake that scripts its notifications
instead.

Two deliberate choices:

* User-visible behaviour is asserted through the running application: a Textual
  pilot drives keys at a fixed terminal size, and the assertions read widget
  state or the application model rather than a rendered string.
* A routing rule with no user-visible shape of its own — the stale-turn guard in
  ``update`` — is asserted one level down, on the update function the application
  calls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import cast

import pytest
from rich.style import Style
from rich.text import Text
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import Static, TextArea

from super_agent.tui import (
    NOTIFICATION_KINDS,
    ROLE_ASSISTANT,
    AgentStatus,
    AgentStatusChanged,
    AgentSummary,
    App,
    Application,
    ApprovalDecision,
    AttachmentSummary,
    Cancellation,
    Channel,
    Command,
    ConversationNotification,
    ConversationNotificationMsg,
    ConversationView,
    MCPServerSummary,
    Message,
    MessageAppended,
    Msg,
    Option,
    PermissionRequest,
    Role,
    SessionSummary,
    StartupInfo,
    StreamChunkReceived,
    ToolApprovalCleared,
    ToolApprovalRequested,
    ToolCall,
    WindowSizeMsg,
    new,
    printCommand,
    update,
    with_output_printer,
)
from super_agent.tui.application import OutputScreen
from super_agent.tui.approval import ApprovalDialog

#: What an update hands back: the commands the shell starts.
type Commands = tuple[Command[Msg], ...]

#: The composer's prompt glyph and the palette's selected-row marker, U+276F and
#: U+203A. Escaped, so the assertions do not read as ambiguous text.
_USER_GLYPH, _SELECTED_MARKER = "\u276f", "\u203a"

_USER = Role("user")


class FakeConversation:
    """The ``Conversation`` port, scripted.

    It holds the scripted state: what ``run_turn`` notifies, what each read
    returns, and what the TUI asked it to do. ``run_turn`` closes the notification
    channel before returning, exactly as the runtime session does when the turn
    ends. ``hold`` keeps a turn in flight — the gate is released by the test —
    which is what makes the running-turn rules observable.
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
        hold: bool = False,
    ) -> None:
        self.script = list(script)
        self.custom_command_map = custom_commands or {}
        self.extra_messages = extra_messages
        self.permission_error = permission_error
        self.mcp_servers = list(mcp_servers)
        self.reject_snapshots = reject_snapshots
        self.run_error = run_error
        self.hold = hold
        self.gate = asyncio.Event()
        self.queries: list[str] = []
        self.cancelled_at_start: list[bool] = []
        self.cancellations: list[Cancellation] = []
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
        self.cancellations.append(cancellation)
        if self.hold:
            await self.gate.wait()
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
    """A turn that asks for approval and ends once a decision arrives."""

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


def new_app(fake: FakeConversation, info: StartupInfo | None = None, *options: Option) -> App:
    """An app whose only dependencies are the fake and the given startup info."""
    return new(fake, info if info is not None else StartupInfo(model_name="test-model"), *options)


async def send(app: App, message: Msg) -> tuple[App, Commands]:
    """One update round: the model and the commands it asked for."""
    return await update(app, message)


async def resize(app: App, width: int, height: int) -> App:
    """Give the app a terminal size, as the first window-size message does."""
    resized, _ = await send(app, WindowSizeMsg(width=width, height=height))
    return resized


def approval_request(tool: str = "bash", command: str = "printf ok") -> ToolApprovalRequested:
    """The notification the runtime sends when a tool call needs a decision."""
    return ToolApprovalRequested(
        tool_call=ToolCall(name=tool, input=command),
        request=PermissionRequest(tool_name=tool, command_class="read-only", cwd="/repo", reason="risky"),
        batch_index=1,
        batch_total=1,
    )


async def open_approval_in(pilot: Pilot[None]) -> None:
    """Submit a prompt whose turn asks for approval, and wait for the prompt.

    ``Pilot`` is parameterised by the application's exit value, which this
    application does not have.
    """
    await pilot.press("g", "o", "enter")
    await pilot.pause()


def static_text(widget: Static) -> str:
    """The plain text a ``Static`` is showing."""
    content = widget.content
    assert isinstance(content, Text), f"the widget is showing {content!r}"
    return content.plain


def status_row(program: Application) -> str:
    """The plain text the status widget is showing."""
    return static_text(program.query_one("#status", Static))


def status_style_at(program: Application, offset: int) -> Style | None:
    """The style covering ``offset`` of the status row, if any span paints it."""
    content = program.query_one("#status", Static).content
    assert isinstance(content, Text), f"the status row is {content!r}"
    for span in content.spans:
        if span.start <= offset < span.end and isinstance(span.style, Style):
            return span.style
    return None


def suggestions_text(program: Application) -> str:
    """The slash-command suggestions the composer is offering."""
    return static_text(program.query_one("#suggestions", Static))


def transcript_text(program: Application) -> str:
    """Every retained transcript block, in the order the pane stacks them."""
    parts: list[str] = []
    for css_class in (".transcript-welcome", ".transcript-message", ".transcript-stream"):
        for widget in program.query(css_class):
            content = cast("Static", widget).content
            # The welcome block is handed over as text; a message block arrives
            # as a styled value.
            if isinstance(content, Text):
                parts.append(content.plain)
            elif isinstance(content, str):
                parts.append(content)
    return "\n".join(parts)


async def settle(pilot: Pilot[None]) -> None:
    """Let a chain of workers — each posting the message that starts the next — finish."""
    for _ in range(3):
        await pilot.pause()


# ---------------------------------------------------------------------------
# Turn lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_shows_busy_presentation_while_model_command_starts() -> None:
    fake = FakeConversation(hold=True)
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"hello", "enter")
        await pilot.pause()

        assert program.model.transcript.busy, "a submitted turn must present as busy"
        assert "Thinking..." in transcript_text(program)

        fake.gate.set()
        await settle(pilot)

    assert fake.queries == ["hello"]


@pytest.mark.asyncio
async def test_tab_completes_unique_slash_command() -> None:
    info = StartupInfo(model_name="test-model", instruction_paths=("/repo/AGENTS.md",))
    program = Application(new_app(FakeConversation(), info))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/ins")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert program.query_one("#composer", TextArea).text == "/instructions", "tab completes the lone match"

        await pilot.press("enter")
        await pilot.pause()
        viewer = program.screen
        assert isinstance(viewer, OutputScreen), "command output opens in the TUI's own viewer"
        assert "Loaded instruction sources" in viewer.content
        assert "/repo/AGENTS.md" in viewer.content


@pytest.mark.asyncio
async def test_workflow_commands_show_diff_and_branch_status() -> None:
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/diff", "enter")
        await pilot.pause()
        assert "Patch preview" in status_row(program)
        viewer = program.screen
        assert isinstance(viewer, OutputScreen)
        assert viewer.content == "diff", "the viewer shows the port's own answer"

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(program.screen, OutputScreen), "escape closes the viewer"

        await pilot.press(*"/branch", "enter")
        await pilot.pause()
        assert "Branch status" in status_row(program)


@pytest.mark.asyncio
async def test_custom_slash_command_expands_arguments() -> None:
    fake = FakeConversation(custom_commands={"audit": "Audit $ARGUMENTS"})
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/audit auth", "enter")
        await pilot.pause()

    assert fake.queries == ["Audit auth"], "a custom command starts a turn"


@pytest.mark.asyncio
async def test_slash_palette_selects_command_with_arrows_and_enter() -> None:
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press("/")
        await pilot.pause()
        palette = suggestions_text(program)
        assert f"{_SELECTED_MARKER} /clear" in palette
        assert "/compact" in palette and "Reset the conversation" in palette

        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert program.query_one("#composer", TextArea).text == "/compact", "enter completes the selection"

        await pilot.press("enter")
        await settle(pilot)
        assert "Compacted conversation" in status_row(program)


@pytest.mark.asyncio
async def test_small_window_keeps_selected_slash_command_visible() -> None:
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(20, 8)) as pilot:
        await pilot.press("/")
        for _ in range(4):
            await pilot.press("down")
        await pilot.pause()

        assert program.model.composer.compactPalette, "a short terminal uses the compact palette"
        palette = suggestions_text(program)
        assert f"{_SELECTED_MARKER} /instructions" in palette
        assert "Show loaded instruction files" not in palette, "compact mode drops the descriptions"


@pytest.mark.asyncio
async def test_escape_clears_input_without_quitting() -> None:
    fake = FakeConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"draft")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert program.query_one("#composer", TextArea).text == ""
        assert "Input cleared" in status_row(program)
        assert program.is_running, "escape clears input, it does not quit"

        await pilot.press("enter")
        await pilot.pause()

    assert fake.queries == [], "a cleared input must not submit"


@pytest.mark.asyncio
async def test_tab_queues_prompt_while_turn_runs() -> None:
    fake = FakeConversation(hold=True)
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"first", "enter")
        await pilot.pause()
        await pilot.press(*"second", "tab")
        await pilot.pause()

        assert "Message queued" in status_row(program)
        assert program.model.composer.queued == ["second"]
        assert fake.cancelled_at_start == [], "queueing must not cancel the running turn"

        fake.gate.set()
        await settle(pilot)
        assert fake.queries == ["first", "second"], "the queued prompt runs when the turn ends"

    assert not fake.cancellations[0].cancelled, "queueing must not cancel the running turn"


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resize_keeps_the_draft_and_the_layout() -> None:
    """``docs/tui.md#layout``: a resize preserves the draft and the layout."""
    info = StartupInfo(model_name="test-model", cwd="/segment" * 30)
    program = Application(new_app(FakeConversation(), info))

    async with program.run_test(size=(40, 12)) as pilot:
        await pilot.press(*"draft")
        await pilot.pause()

        await pilot.resize_terminal(100, 30)
        await pilot.pause()

        assert program.size.width == 100
        assert program.query_one("#composer", TextArea).text == "draft"
        assert program.query_one("#transcript", VerticalScroll).size.width > 0
        assert "test-model" in status_row(program)


@pytest.mark.asyncio
async def test_welcome_is_part_of_managed_transcript() -> None:
    info = StartupInfo(model_name="test-model", version="0.1.0", cwd="/repo", instruction_paths=("/repo/AGENTS.md",))
    program = Application(new_app(FakeConversation(), info))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        shown = transcript_text(program)

    assert ">_ Super Agent (v0.1.0)" in shown
    assert "model:       test-model" in shown
    assert "directory:   /repo" in shown
    assert "instructions: AGENTS.md" in shown


@pytest.mark.asyncio
async def test_welcome_card_and_composer_match_the_terminal_layout() -> None:
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        welcome = program.query_one(".transcript-welcome", Static)
        tip = program.query_one(".welcome-tip", Static)
        composer = program.query_one("#composer", TextArea)

        assert welcome.styles.border.top[0] == "round"
        assert static_text(tip).startswith("Tip:")
        assert composer.placeholder == "\u276f Ask Super Agent to do anything"


# ---------------------------------------------------------------------------
# Cancellation and steering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_esc_cancels_turn_and_clears_queued_follow_ups() -> None:
    fake = FakeConversation(hold=True)
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"active", "enter")
        await pilot.pause()
        await pilot.press(*"follow-up", "tab")
        await pilot.pause()
        assert program.model.composer.queued == ["follow-up"]

        await pilot.press("escape")
        await pilot.pause()

        assert "Turn canceled" in status_row(program)
        assert program.model.composer.queued == [], "a manual cancellation drops the queue"
        assert fake.cancellations[0].cancelled, "the runtime is told to drop the turn"

        fake.gate.set()
        await settle(pilot)

    assert fake.queries == ["active"], "a manual cancellation must not run the queued follow-up"


@pytest.mark.asyncio
async def test_enter_steers_by_canceling_current_turn_and_running_prompt_next() -> None:
    fake = FakeConversation(hold=True)
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"first", "enter")
        await pilot.pause()
        await pilot.press(*"steer", "enter")
        await pilot.pause()

        assert "Steering current turn" in status_row(program)
        assert fake.cancellations[0].cancelled, "steering cancels the turn in flight"

        fake.gate.set()
        await settle(pilot)

    assert fake.queries == ["first", "steer"], "the steering prompt runs when the cancelled turn ends"


@pytest.mark.asyncio
async def test_ctrl_j_inserts_newline_and_enter_submits() -> None:
    fake = FakeConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"first line")
        await pilot.press("ctrl+j")
        await pilot.press(*"second line")
        await pilot.press("enter")
        await pilot.pause()

    assert fake.queries == ["first line\nsecond line"], fake.queries


@pytest.mark.asyncio
async def test_history_navigation_restores_unsubmitted_draft() -> None:
    fake = FakeConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"previous", "enter")
        await pilot.pause()
        await pilot.press(*"draft")
        await pilot.pause()

        await pilot.press("up")
        await pilot.pause()
        assert program.query_one("#composer", TextArea).text == "previous"

        await pilot.press("down")
        await pilot.pause()
        assert program.query_one("#composer", TextArea).text == "draft", "the unsubmitted draft must come back"


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_opens_a_modal_that_owns_the_keyboard() -> None:
    """The shortcut keys answer the prompt instead of reaching the composer.

    The editor inserts a printable key before any application handler sees it, so
    the answer keys must belong to a surface that already has focus.
    """
    fake = ApprovalConversation(clears=False)
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await open_approval_in(pilot)
        prompt = program.screen
        assert isinstance(prompt, ApprovalDialog), "the prompt is a modal screen"
        assert "ACTION REQUIRED" in prompt.view().plain

        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()
        assert program.query_one("#composer", TextArea).text == "", "the modal swallows stray keys"

        await pilot.press("y")
        await pilot.pause()
        assert program.query_one("#composer", TextArea).text == "", "a shortcut is not text"
        assert isinstance(program.screen, ApprovalDialog), "the prompt stays up until the runtime moves on"

    assert fake.decisions == [ApprovalDecision("once")]


@pytest.mark.asyncio
async def test_approval_selection_answers_the_highlighted_row() -> None:
    fake = ApprovalConversation(clears=False)
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await open_approval_in(pilot)
        await pilot.press("down", "down", "enter")
        await pilot.pause()
        assert fake.decisions == [ApprovalDecision("deny")]

        # The latch holds: a key after the answer cannot answer the next request.
        await pilot.press("y")
        await pilot.pause()
        assert fake.decisions == [ApprovalDecision("deny")]

    assert "Decision submitted" in program.model.approval.view("/repo").plain


@pytest.mark.asyncio
async def test_escape_answers_the_prompt_by_cancelling_the_turn() -> None:
    fake = ApprovalConversation(clears=False)
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await open_approval_in(pilot)
        await pilot.press("escape")
        await pilot.pause()

    assert fake.decisions == [], "leaving the prompt is not an answer"
    assert fake.cancels == 1, "the runtime is told to drop the pending request"


@pytest.mark.asyncio
async def test_closing_the_prompt_gives_the_keyboard_back() -> None:
    fake = ApprovalConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await open_approval_in(pilot)
        await pilot.press("y")
        await pilot.pause()
        assert not isinstance(program.screen, ApprovalDialog), "the runtime moved on"
        assert program.query_one("#composer", TextArea).has_focus

        await pilot.press("n", "e", "x", "t")
        await pilot.pause()
        assert program.query_one("#composer", TextArea).text == "next"

    assert fake.decisions == [ApprovalDecision("once")]


# ---------------------------------------------------------------------------
# Notifications and rendering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tui_renders_session_notifications_without_snapshot_reads() -> None:
    fake = FakeConversation(
        script=[
            AgentStatusChanged(status=AgentStatus(label="WaitingLLM", busy=True)),
            MessageAppended(message=Message(role=_USER, content="hello")),
            MessageAppended(message=Message(role=ROLE_ASSISTANT, content="from notification")),
            AgentStatusChanged(status=AgentStatus(label="Idle")),
        ],
        reject_snapshots=True,
    )
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"hello", "enter")
        await pilot.pause()

        shown = transcript_text(program)
        assert f"{_USER_GLYPH} hello" in shown
        assert "from notification" in shown


def test_conversation_declares_seven_notification_kinds() -> None:
    """The sealed set is the port's contract; a drift here must fail loudly."""
    assert NOTIFICATION_KINDS == (
        "AgentStatusChanged",
        "ToolApprovalRequested",
        "ToolApprovalCleared",
        "StreamChunkReceived",
        "MessageAppended",
        "UsageReported",
        "ConversationError",
    )


@pytest.mark.asyncio
async def test_stale_notification_is_dropped_and_the_listener_re_armed() -> None:
    """The turn guard in ``update``: it has no user-visible shape of its own."""
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    stale = ConversationNotificationMsg(
        notification=MessageAppended(message=Message(role=ROLE_ASSISTANT, content="stale")),
        turn=app.turn - 1,
    )

    app, commands = await send(app, stale)
    assert len(commands) == 1, "the listener is re-armed on the current channel"
    assert app.transcript.messages == [], "a notification from a replaced turn is dropped"


@pytest.mark.asyncio
async def test_tool_calls_are_summarized_and_expand_on_demand() -> None:
    fake = FakeConversation(
        script=[
            MessageAppended(message=Message(role=_USER, content="inspect")),
            MessageAppended(
                message=Message(
                    role=ROLE_ASSISTANT,
                    content="Answer",
                    reasoning_content="private reasoning",
                    tool_calls=(
                        ToolCall(name="read_file", input='{"path": "tui/app.py"}'),
                        ToolCall(name="read_file", input='{"path": "tui/update.py"}'),
                    ),
                )
            ),
            MessageAppended(
                message=Message(
                    role=ROLE_ASSISTANT,
                    reasoning_content="patch reasoning",
                    tool_calls=(ToolCall(name="apply_patch", input='{"path": "tui/statusline.py"}'),),
                )
            ),
        ]
    )
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"inspect", "enter")
        await pilot.pause()

        rendered = transcript_text(program)
        assert "● Read 2 files" in rendered and "● Edited tui/statusline.py" in rendered
        assert "private reasoning" not in rendered, "reasoning stays collapsed by default"
        assert '{"path"' not in rendered, "raw tool inputs stay hidden"

        await pilot.press("ctrl+o")
        await pilot.pause()
        latest = transcript_text(program)
        assert "tui/statusline.py" in latest and "tui/app.py" not in latest, "only the latest group expands"

        await pilot.press("alt+o")
        await pilot.pause()
        expanded = transcript_text(program)
        assert "tui/app.py" in expanded and "tui/statusline.py" in expanded
        assert expanded.index("● Read") < expanded.index("tui/app.py") < expanded.index("● Edited"), (
            "expanded details stay below their tool call"
        )

        await pilot.press("ctrl+r")
        await pilot.pause()
        latest_thinking = transcript_text(program)
        assert "patch reasoning" in latest_thinking and "private reasoning" not in latest_thinking

        await pilot.press("alt+r")
        await pilot.pause()
        all_thinking = transcript_text(program)
        assert "patch reasoning" in all_thinking and "private reasoning" in all_thinking


@pytest.mark.asyncio
async def test_page_keys_move_the_transcript_viewport() -> None:
    """``docs/tui.md#keys``: PgUp and PgDn move the transcript viewport by a page."""
    model = new_app(FakeConversation())
    for index in range(40):
        model.transcript.append(Message(role=ROLE_ASSISTANT, content=f"message {index}\n" * 2))
    program = Application(model)

    async with program.run_test(size=(60, 16)) as pilot:
        pane = program.query_one("#transcript", VerticalScroll)
        await pilot.pause()
        pane.scroll_end(animate=False)
        await pilot.pause()
        bottom = pane.scroll_y
        assert bottom > 0, "the transcript is taller than the viewport"

        await pilot.press("pageup")
        await pilot.pause()
        paged_up = pane.scroll_y
        assert paged_up < bottom, "PgUp moves the viewport up"

        await pilot.press("pagedown")
        await pilot.pause()
        assert pane.scroll_y > paged_up, "PgDn moves the viewport back down"


@pytest.mark.asyncio
async def test_stream_chunk_replaces_the_streaming_message() -> None:
    fake = FakeConversation(hold=True)
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"hello", "enter")
        await pilot.pause()
        assert "Thinking..." in transcript_text(program)

        program.model.notifications.put(StreamChunkReceived(message=Message(role=ROLE_ASSISTANT, content="streaming")))
        await pilot.pause()

        shown = transcript_text(program)
        assert "streaming" in shown and "Thinking..." not in shown, "the chunk replaces the placeholder"

        fake.gate.set()
        await settle(pilot)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_command_output_goes_to_the_installed_printer() -> None:
    """The shell installs the viewer that draws command output; without one it is dropped."""
    printed: list[str] = []

    def recording(content: str) -> Command[Msg]:
        async def show() -> None:
            printed.append(content)

        return show

    app = new_app(FakeConversation(), StartupInfo(model_name="test-model"), with_output_printer(recording))
    assert printCommand(app, "  \n") is None, "blank output has nothing to show"

    command = printCommand(app, " diff \n")
    assert command is not None, "the viewer is what draws command output"
    await command()
    assert printed == [" diff "], "only the trailing newline the status line owns is trimmed"

    assert printCommand(new_app(FakeConversation()), "diff") is None, "no viewer installed, nothing to draw into"


@pytest.mark.asyncio
async def test_instructions_command_displays_loaded_sources() -> None:
    info = StartupInfo(model_name="test-model", instruction_paths=("/repo/AGENTS.md", "/repo/pkg/CLAUDE.md"))
    program = Application(new_app(FakeConversation(), info))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/instructions", "enter")
        await pilot.pause()

        viewer = program.screen
        assert isinstance(viewer, OutputScreen)
        assert "Loaded instruction sources" in viewer.content
        assert "/repo/AGENTS.md" in viewer.content and "/repo/pkg/CLAUDE.md" in viewer.content


@pytest.mark.asyncio
async def test_permissions_mode_command_rejects_invalid_mode() -> None:
    fake = FakeConversation(permission_error=ValueError("invalid permission mode: root"))
    info = StartupInfo(model_name="test-model", permission_mode="ask")
    program = Application(new_app(fake, info))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/permissions mode root", "enter")
        await pilot.pause()

        shown = status_row(program)
        assert "error: Permissions failed: invalid permission mode: root" in shown
        assert program.model.info.permission_mode == "ask", "the mode stays as it was"

    assert fake.permission_mode() == "root", "the port was asked, and refused"


@pytest.mark.asyncio
async def test_permissions_mode_keeps_displayed_model() -> None:
    fake = FakeConversation()
    program = Application(new_app(fake, StartupInfo(model_name="test-model", permission_mode="ask")))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/permissions mode plan", "enter")
        await pilot.pause()
        assert fake.permission_mode() == "plan"
        viewer = program.screen
        assert isinstance(viewer, OutputScreen)
        assert "Permission mode: plan" in viewer.content

        # The command's own status line holds the row until it is cleared, so
        # only then does the configured row show what the mode became.
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert status_row(program) == "test-model · plan", "the mode changes without losing the model"


@pytest.mark.asyncio
async def test_attach_command_routes_through_the_attachments_feature() -> None:
    fake = FakeConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/attach notes.md", "enter")
        await settle(pilot)

        assert "Attached notes.md (text/plain)" in status_row(program)

    assert fake.attached_paths == ["notes.md"]


@pytest.mark.asyncio
async def test_mcp_commands_list_and_add_server() -> None:
    fake = FakeConversation(mcp_servers=[MCPServerSummary(name="files", tools=("read_remote",))])
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press(*"/mcp list", "enter")
        await pilot.pause()
        viewer = program.screen
        assert isinstance(viewer, OutputScreen)
        assert "files  read_remote" in viewer.content

        await pilot.press("escape")
        await pilot.pause()
        await pilot.press(*"/mcp add local helper --stdio", "enter")
        await settle(pilot)

        assert fake.mcp_added == ("local", "helper", ["--stdio"])
        assert "Added MCP server local" in status_row(program)


# ---------------------------------------------------------------------------
# The Textual application
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_textual_application_starts_with_composer_focus_and_submits() -> None:
    fake = FakeConversation()
    program = Application(new_app(fake))

    async with program.run_test(size=(80, 24)) as pilot:
        composer = program.query_one("#composer", TextArea)
        assert composer.has_focus
        await pilot.press("a", "n", "y", "1", "2", "3", "j", "k", "enter")
        await pilot.pause()

    assert fake.queries == ["any123jk"]


@pytest.mark.asyncio
async def test_textual_transcript_is_a_scrollable_viewport() -> None:
    model = new_app(FakeConversation())
    for index in range(40):
        model.transcript.append(Message(role=ROLE_ASSISTANT, content=f"message {index}\n" * 2))
    program = Application(model)

    async with program.run_test(size=(60, 16)) as pilot:
        pane = program.query_one("#transcript", VerticalScroll)
        await pilot.pause()
        assert pane.max_scroll_y > 0
        pane.scroll_end(animate=False)
        await pilot.pause()
        bottom = pane.scroll_y
        await pilot.press("pageup")
        await pilot.pause()
        assert pane.scroll_y < bottom


@pytest.mark.asyncio
async def test_ctrl_u_clears_the_draft() -> None:
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()
        await pilot.press("ctrl+u")
        await pilot.pause()
        assert program.query_one("#composer", TextArea).text == ""

    assert program.model.composer.draft() == ""


@pytest.mark.asyncio
async def test_ctrl_l_returns_to_the_latest_output() -> None:
    model = new_app(FakeConversation())
    for index in range(40):
        model.transcript.append(Message(role=ROLE_ASSISTANT, content=f"message {index}\n" * 2))
    model.status = "Compacting conversation…"
    program = Application(model)

    async with program.run_test(size=(60, 16)) as pilot:
        pane = program.query_one("#transcript", VerticalScroll)
        await pilot.pause()
        await pilot.press("pageup")
        await pilot.pause()
        assert not pane.is_vertical_scroll_end, "the viewport is pinned above the end"

        await pilot.press("ctrl+l")
        await pilot.pause()
        assert pane.is_vertical_scroll_end, "the latest content comes back"

    assert program.model.status == "", "the transient status line goes with it"


@pytest.mark.asyncio
async def test_question_mark_opens_help_only_without_a_draft() -> None:
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(program.screen, OutputScreen), "an empty draft leaves ? for help"

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(program.screen, OutputScreen)

        await pilot.press("w", "h", "y", "?")
        await pilot.pause()
        assert not isinstance(program.screen, OutputScreen), "a draft keeps ? for itself"
        assert program.query_one("#composer", TextArea).text == "why?"


@pytest.mark.asyncio
async def test_alt_o_and_alt_r_toggle_every_group() -> None:
    program = Application(new_app(FakeConversation()))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.press("alt+o")
        await pilot.press("alt+r")
        await pilot.pause()

    assert program.model.transcript.expandAllTools
    assert program.model.transcript.expandAllThink


@pytest.mark.asyncio
async def test_status_row_is_the_configured_items() -> None:
    """The row is ``tui.status_line`` composed, not a fixed trio."""
    info = StartupInfo(model_name="test-model", permission_mode="ask", cwd="/repo", status_line=("cwd", "model"))
    program = Application(new_app(FakeConversation(), info))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert status_row(program) == "/repo · test-model"


@pytest.mark.asyncio
async def test_status_row_is_hidden_when_the_setting_removes_it() -> None:
    """``null`` returns the row's height to the transcript rather than drawing it blank."""
    program = Application(new_app(FakeConversation(), StartupInfo(model_name="test-model", status_line=())))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert program.query_one("#status", Static).display is False


@pytest.mark.asyncio
async def test_an_item_without_data_is_left_out_of_the_row() -> None:
    """A configured item the process never learned costs neither text nor a separator."""
    info = StartupInfo(model_name="test-model", permission_mode="", status_line=("model", "approval"))
    program = Application(new_app(FakeConversation(), info))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert status_row(program) == "test-model"


@pytest.mark.asyncio
async def test_the_error_line_takes_the_row_from_the_items() -> None:
    """``docs/tui.md`` gives a command's error precedence over the status line."""
    info = StartupInfo(model_name="test-model", status_line=("model",))
    program = Application(new_app(FakeConversation(), info))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert "test-model" in status_row(program)

        program.model.err = "boom"
        await pilot.press("up")
        await pilot.pause()

        shown = status_row(program)
        assert "boom" in shown
        assert "test-model" not in shown, "the error replaces the row rather than joining it"


@pytest.mark.asyncio
async def test_the_spinner_item_follows_the_agent_state() -> None:
    """The engine reports live states, so the row follows them as they happen."""
    info = StartupInfo(status_line=("spinner",))
    program = Application(new_app(FakeConversation(), info))

    async with program.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert status_row(program) == "Idle"
        assert status_style_at(program, 0) == program.model.styles.secondary, "idle is not urgent"

        program.model.agentStatus = AgentStatus(label="RunningTool", busy=True)
        await pilot.press("up")
        await pilot.pause()
        assert status_row(program) == "RunningTool"
        assert status_style_at(program, 0) == program.model.styles.accent, "work in progress takes the accent"

        program.model.agentStatus = AgentStatus(label="WaitingApproval", awaiting_approval=True)
        await pilot.press("up")
        await pilot.pause()
        assert status_style_at(program, 0) == program.model.styles.accent_bold, "nothing moves until they answer"
