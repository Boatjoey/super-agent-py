"""The TUI's update, routing, view, and turn lifecycle.

The tests drive the real engine and session through ``app.new_tui_conversation``;
the TUI may not import ``runtime`` (R1), so the double defined here is a fake
that scripts its notifications instead. The behaviours under test: routing
order, the stale-turn guard, the composer's intents, the approval latch, and the
width and height invariants.

Two deliberate choices:

* Nothing asserts a rendered string. Rich measures and wraps text, so a view is
  asserted by the properties it must keep — every line is at most ``width``
  cells, the tail survives the height window, and the text carries the words the
  user must see. ``render`` measures through a fixed-width console, so the result
  cannot depend on the developer's terminal.
* Commands are awaited rather than run concurrently. A command is an awaitable,
  and a test awaits it and feeds the result back through ``update`` itself, which
  is the same round trip the runtime performs.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
from collections.abc import Sequence
from typing import cast

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.protocol import is_renderable
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widgets import TextArea

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
    KeyDecoder,
    KeyMsg,
    Listener,
    MCPServerSummary,
    Message,
    MessageAppended,
    Msg,
    Option,
    OutputPrinter,
    PermissionRequest,
    Program,
    Role,
    SessionSummary,
    StartupInfo,
    StreamChunkReceived,
    ToolApprovalCleared,
    ToolApprovalRequested,
    ToolCall,
    WindowSizeMsg,
    new,
    update,
    view,
    with_output_printer,
)
from super_agent.tui.application import OutputScreen
from super_agent.tui.approval import ApprovalDialog

#: What an update hands back: the commands the runtime should start.
type Commands = tuple[Command[Msg], ...]

#: The measurement console: wide enough that Rich never re-wraps a view, so a
#: line measured here is the line the app produced.
_MEASURE_WIDTH = 4000

#: The composer's prompt glyph and the palette's selected-row marker, U+276F and
#: U+203A. Escaped, so the assertions do not read as ambiguous text.
_USER_GLYPH, _SELECTED_MARKER = "\u276f", "\u203a"

_USER = Role("user")


def render(renderable: object) -> str:
    """The plain text of a renderable, measured at a fixed width."""
    console = Console(
        width=_MEASURE_WIDTH,
        file=io.StringIO(),
        force_terminal=False,
        color_system=None,
        legacy_windows=False,
    )
    assert is_renderable(renderable), f"{renderable!r} is not renderable"
    with console.capture() as capture:
        console.print(renderable, end="", markup=False, highlight=False)
    return capture.get()


def assert_lines_fit_width(renderable: object, width: int) -> None:
    """Every line of ``renderable`` is at most ``width`` terminal cells."""
    for index, line in enumerate(render(renderable).split("\n")):
        assert cell_len(line) <= width, f"line {index} is {cell_len(line)} cells wide, terminal is {width}: {line!r}"


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


def new_app(fake: FakeConversation, info: StartupInfo | None = None, *options: Option) -> App:
    """An app whose only dependencies are the fake and the given startup info."""
    return new(fake, info if info is not None else StartupInfo(model_name="test-model"), *options)


def recording_printer(printed: list[str]) -> OutputPrinter:
    """A printer that records scrollback instead of writing it."""

    def print_output(content: str) -> Command[Msg] | None:
        printed.append(content)
        return None

    return print_output


async def send(app: App, message: Msg) -> tuple[App, Commands]:
    """One update round: the model and the commands it asked for."""
    return await update(app, message)


async def resize(app: App, width: int, height: int) -> App:
    """Give the app a terminal size, as the first window-size message does."""
    resized, _ = await send(app, WindowSizeMsg(width=width, height=height))
    return resized


async def type_text(app: App, text: str) -> App:
    """Type ``text`` one key at a time, as a terminal would deliver it."""
    for character in text:
        app, _ = await send(app, KeyMsg(key=character))
    return app


async def press(app: App, key: str) -> tuple[App, Commands]:
    """One key press, with the commands it produced."""
    return await send(app, KeyMsg(key=key))


async def drain(app: App, listener: Listener[Msg]) -> App:
    """Deliver every queued notification on the current turn's channel.

    The loop stops when the channel closes — which is what ends the listener
    chain — or when a notification starts a new turn, whose listener belongs to a
    channel nothing has written to yet.
    """
    current: Command[Msg] | None = listener
    while current is not None and app.notifications.closed:
        message = await current()
        if message is None:
            return app
        turn = app.turn
        app, commands = await send(app, message)
        current = commands[0] if commands and app.turn == turn else None
    return app


async def start_turn(app: App, text: str) -> tuple[App, Listener[Msg], Command[Msg]]:
    """Submit ``text`` and return the app, its listener, and its run command."""
    app = await type_text(app, text)
    app, commands = await press(app, "enter")
    assert len(commands) == 2, f"a submission is a listener and a run, got {commands!r}"
    return app, cast("Listener[Msg]", commands[0]), commands[-1]


async def settle(app: App, listener: Listener[Msg], run: Command[Msg]) -> tuple[App, Commands]:
    """Run a turn to completion and fold its notifications back in.

    Returns the commands the finished turn produced, which is how a test sees a
    queued prompt start the next one.
    """
    message = await run()
    assert message is not None, "the run command must report its outcome"
    app = await drain(app, listener)
    return await send(app, message)


def notification(app: App, value: ConversationNotification) -> ConversationNotificationMsg:
    """A notification tagged with the turn in flight, as the listener delivers it."""
    return ConversationNotificationMsg(notification=value, turn=app.turn)


# ---------------------------------------------------------------------------
# Turn lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_shows_busy_presentation_while_model_command_starts() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app, listener, run = await start_turn(app, "hello")

    assert "Thinking..." in render(view(app)), "a submitted turn must present as busy"

    app, _ = await settle(app, listener, run)
    assert fake.queries == ["hello"]


@pytest.mark.asyncio
async def test_question_mark_can_be_typed_in_prompt() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app, listener, run = await start_turn(app, "what?")
    app, _ = await settle(app, listener, run)

    assert fake.queries == ["what?"], "the question mark belongs to the prompt"


@pytest.mark.asyncio
async def test_tab_completes_unique_slash_command() -> None:
    printed: list[str] = []
    fake = FakeConversation()
    info = StartupInfo(model_name="test-model", instruction_paths=("/repo/AGENTS.md",))
    app = await resize(new(fake, info, with_output_printer(recording_printer(printed))), 80, 24)
    app = await type_text(app, "/ins")
    app, _ = await press(app, "tab")
    app, _ = await press(app, "enter")

    assert "Instructions" in render(view(app))
    assert any("Loaded instruction sources" in item for item in printed), printed


@pytest.mark.asyncio
async def test_workflow_commands_show_diff_and_branch_status() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)

    app = await type_text(app, "/diff")
    app, commands = await press(app, "enter")
    assert "Patch preview" in render(view(app))
    assert len(commands) == 1, "the patch goes to scrollback rather than the live view"
    await commands[0]()  # the scrollback command, with no console attached

    app = await type_text(app, "/branch")
    app, _ = await press(app, "enter")
    assert "Branch status" in render(view(app))


@pytest.mark.asyncio
async def test_custom_slash_command_expands_arguments() -> None:
    fake = FakeConversation(custom_commands={"audit": "Audit $ARGUMENTS"})
    app = await resize(new_app(fake), 80, 24)
    app, listener, run = await start_turn(app, "/audit auth")

    app, _ = await settle(app, listener, run)
    assert fake.queries == ["Audit auth"], "a custom command starts a turn"


@pytest.mark.asyncio
async def test_slash_palette_selects_command_with_arrows_and_enter() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app = await type_text(app, "/")
    palette = render(view(app))
    assert f"{_SELECTED_MARKER} /clear" in palette
    assert "/compact" in palette and "Reset the conversation" in palette

    app, _ = await press(app, "down")
    app, _ = await press(app, "enter")
    assert f"{_SELECTED_MARKER} /compact" in render(view(app)), "the selection is completed into the composer"

    app, commands = await press(app, "enter")
    assert "Compacting conversation" in render(view(app))
    assert len(commands) == 1, commands

    message = await commands[0]()
    assert message is not None
    app, _ = await send(app, message)
    assert "Compacted conversation" in render(view(app))


@pytest.mark.asyncio
async def test_small_window_keeps_selected_slash_command_visible() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 20, 8)
    app = await type_text(app, "/")
    for _ in range(4):
        app, _ = await press(app, "down")

    palette = render(view(app))
    assert f"{_SELECTED_MARKER} /instructions" in palette
    assert "Show loaded instruction files" not in palette, "a short terminal uses the compact palette"


@pytest.mark.asyncio
async def test_escape_clears_input_without_quitting() -> None:
    app = await resize(new_app(FakeConversation()), 80, 24)
    app = await type_text(app, "draft")
    app, commands = await press(app, "esc")

    assert commands == (), "escape clears input, it does not quit"
    assert "Input cleared" in render(view(app))

    app, commands = await press(app, "enter")
    assert commands == (), "a cleared input must not submit"


@pytest.mark.asyncio
async def test_tab_queues_prompt_while_turn_runs() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app, listener, run = await start_turn(app, "first")

    app = await type_text(app, "second")
    app, commands = await press(app, "tab")
    assert commands == (), "queueing must not start a concurrent turn"
    queued = render(view(app))
    assert "Message queued" in queued and "Queued (1)" in queued and "1. second" in queued

    app, commands = await settle(app, listener, run)
    assert len(commands) == 2, "the queued prompt starts the next turn"
    await commands[-1]()
    assert fake.queries == ["first", "second"], "the queued prompt runs when the turn ends"
    assert fake.cancelled_at_start == [False, False], "queueing must not cancel the running turn"


@pytest.mark.asyncio
async def test_queue_preview_is_bounded() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app, _listener, _run = await start_turn(app, "active")
    for prompt in ("one", "two", "three", "four"):
        app = await type_text(app, prompt)
        app, _ = await press(app, "tab")

    rendered = render(view(app))
    for wanted in ("Queued (4)", "1. one", "2. two", "3. three", "… 1 more"):
        assert wanted in rendered, f"{wanted!r} missing from {rendered!r}"
    assert "4. four" not in rendered, "the preview shows at most three items"


@pytest.mark.asyncio
async def test_small_window_collapses_queue_details() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 20, 8)
    app, _listener, _run = await start_turn(app, "active")
    for prompt in ("one", "two", "three", "four"):
        app = await type_text(app, prompt)
        app, _ = await press(app, "tab")

    rendered = render(view(app))
    assert "Queued (4)" in rendered and "… 4 more" in rendered
    assert "1. one" not in rendered, "a short terminal hides the queue details"


# ---------------------------------------------------------------------------
# Layout invariants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrow_window_clamps_every_rendered_line() -> None:
    info = StartupInfo(model_name="test-model", cwd="/segment" * 30)
    app = await resize(new_app(FakeConversation(), info), 30, 12)
    assert_lines_fit_width(view(app), 30)


@pytest.mark.asyncio
async def test_info_bar_keeps_mode_when_working_directory_is_long() -> None:
    info = StartupInfo(model_name="test-model", cwd="/segment" * 30)
    app = await resize(new_app(FakeConversation(), info), 40, 24)
    rendered = view(app)
    assert "ask · test-model · tools on" in render(rendered)
    assert_lines_fit_width(rendered, 40)


@pytest.mark.asyncio
async def test_overlong_error_is_clamped_not_wrapped() -> None:
    fake = FakeConversation(permission_error=ValueError("boom" * 40))
    info = StartupInfo(model_name="test-model", permission_mode="ask")
    app = await resize(new_app(fake, info), 40, 24)
    app = await type_text(app, "/permissions mode root")
    app, _ = await press(app, "enter")

    rendered = view(app)
    assert "!! error:" in render(rendered)
    assert_lines_fit_width(rendered, 40)


@pytest.mark.asyncio
async def test_narrow_window_keeps_composer_visible() -> None:
    info = StartupInfo(model_name="test-model", cwd="/segment" * 30)
    app = await resize(new_app(FakeConversation(), info), 30, 10)
    rendered = view(app)
    assert "Ask me anything" in render(rendered), "the composer survives the height window"
    assert_lines_fit_width(rendered, 30)


@pytest.mark.asyncio
async def test_queued_preview_is_clamped_to_terminal_width() -> None:
    app = await resize(new_app(FakeConversation()), 24, 12)
    app, _listener, _run = await start_turn(app, "active")
    app = await type_text(app, "queued prompt " * 6)
    app, _ = await press(app, "tab")

    assert_lines_fit_width(view(app), 24)


@pytest.mark.asyncio
async def test_help_overlay_is_clamped_to_terminal_width() -> None:
    app = await resize(new_app(FakeConversation()), 20, 8)
    app, _ = await press(app, "?")
    assert_lines_fit_width(view(app), 20)


@pytest.mark.asyncio
async def test_resize_recomputes_clamped_budget() -> None:
    info = StartupInfo(model_name="test-model", cwd="/segment" * 30)
    app = await resize(new_app(FakeConversation(), info), 24, 14)
    assert_lines_fit_width(view(app), 24)
    app = await resize(app, 60, 20)
    assert_lines_fit_width(view(app), 60)


@pytest.mark.asyncio
async def test_welcome_is_part_of_managed_transcript() -> None:
    info = StartupInfo(model_name="test-model", cwd="/repo", instruction_paths=("/repo/AGENTS.md",))
    app = await resize(new_app(FakeConversation(), info), 80, 24)
    rendered = render(view(app))
    assert "Super Agent" in rendered
    assert "test-model · /repo · AGENTS.md" in rendered


@pytest.mark.asyncio
async def test_status_line_keeps_model_and_mode() -> None:
    app = await resize(new_app(FakeConversation()), 50, 24)
    rendered = view(app)
    assert "ask · test-model · tools on" in render(rendered)
    assert_lines_fit_width(rendered, 50)


# ---------------------------------------------------------------------------
# Cancellation and steering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_esc_cancels_turn_and_clears_queued_follow_ups() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app, _listener, run = await start_turn(app, "active")
    app = await type_text(app, "follow-up")
    app, _ = await press(app, "tab")
    app, commands = await press(app, "esc")

    assert commands == ()
    cancelled = render(view(app))
    assert "Turn canceled" in cancelled and "Queued (1)" not in cancelled

    message = await run()
    assert message is not None
    app, commands = await send(app, message)
    assert commands == (), "a manual cancellation must not run the queued follow-up"
    assert fake.queries == ["active"], fake.queries


@pytest.mark.asyncio
async def test_enter_steers_by_canceling_current_turn_and_running_prompt_next() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app, _listener, run = await start_turn(app, "first")

    app = await type_text(app, "steer")
    app, commands = await press(app, "enter")
    assert commands == (), "steering waits for the cancelled turn to finish"
    assert "Steering current turn" in render(view(app))

    message = await run()
    assert message is not None
    app, commands = await send(app, message)
    assert len(commands) == 2, commands
    await commands[-1]()
    assert fake.queries == ["first", "steer"]
    assert fake.cancelled_at_start[0] is True, "steering cancels the turn in flight"


@pytest.mark.asyncio
async def test_ctrl_j_inserts_newline_and_enter_submits() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app = await type_text(app, "first line")
    app, _ = await press(app, "ctrl+j")
    app = await type_text(app, "second line")
    app, listener, run = await start_turn(app, "")

    app, _ = await settle(app, listener, run)
    assert fake.queries == ["first line\nsecond line"], fake.queries


@pytest.mark.asyncio
async def test_history_navigation_restores_unsubmitted_draft() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app, listener, run = await start_turn(app, "previous")
    app, _ = await settle(app, listener, run)

    app = await type_text(app, "draft")
    app, _ = await press(app, "up")
    assert f"{_USER_GLYPH} previous" in render(view(app))
    app, _ = await press(app, "down")
    assert f"{_USER_GLYPH} draft" in render(view(app)), "the unsubmitted draft must come back"


# ---------------------------------------------------------------------------
# Approval
# ---------------------------------------------------------------------------


def approval_request(tool: str = "bash", command: str = "printf ok") -> ToolApprovalRequested:
    """The notification the runtime sends when a tool call needs a decision."""
    return ToolApprovalRequested(
        tool_call=ToolCall(name=tool, input=command),
        request=PermissionRequest(tool_name=tool, command_class="read-only", cwd="/repo", reason="risky"),
        batch_index=1,
        batch_total=1,
    )


async def open_approval(app: App, fake: FakeConversation) -> App:
    """Start a turn whose script asks for approval, and show the menu."""
    fake.script = [cast("ConversationNotification", approval_request())]
    app, listener, run = await start_turn(app, "run bash")
    await run()
    return await drain(app, listener)


@pytest.mark.asyncio
async def test_approval_uses_shortcut_keys() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app = await open_approval(app, fake)
    assert "ACTION REQUIRED" in render(view(app))

    app, commands = await press(app, "y")
    assert commands == ()
    decision = await asyncio.wait_for(app.approvals.get(), timeout=1)
    assert decision == "once", decision


@pytest.mark.asyncio
async def test_tool_run_clears_approval_presentation() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 120, 24)
    app = await open_approval(app, fake)
    app, _ = await press(app, "y")

    app, commands = await send(app, notification(app, ToolApprovalCleared()))
    assert commands, "the listener must be re-armed"
    rendered = render(view(app))
    assert "ACTION REQUIRED" not in rendered, "the menu closes once the runtime moves on"
    assert "Thinking..." in rendered, "the busy placeholder returns while the tool runs"


@pytest.mark.asyncio
async def test_approval_menu_uses_arrows_and_enter() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app = await open_approval(app, fake)

    app, _ = await press(app, "down")
    app, _ = await press(app, "down")
    assert f"{_SELECTED_MARKER} 3. No, deny" in render(view(app))

    app, _ = await press(app, "enter")
    assert "Decision submitted" in render(view(app))
    decision = await asyncio.wait_for(app.approvals.get(), timeout=1)
    assert decision == "deny", decision


@pytest.mark.asyncio
async def test_esc_cancels_pending_approval() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app = await open_approval(app, fake)

    app, commands = await press(app, "esc")
    assert commands == (), "escape cancels the turn rather than returning a command"
    assert fake.cancels == 1, "the runtime is told to drop the pending request"
    assert app.cancellation is not None
    assert app.cancellation.cancelled


@pytest.mark.asyncio
async def test_approval_latch_ignores_repeated_keys() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app = await open_approval(app, fake)

    app, _ = await press(app, "y")
    app, _ = await press(app, "n")
    decision = await asyncio.wait_for(app.approvals.get(), timeout=1)
    assert decision == "once", "a submitted decision ignores later keys"


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
        ]
    )
    app = await resize(new_app(fake), 80, 24)
    fake.reject_snapshots = True
    app, listener, run = await start_turn(app, "hello")
    app, _ = await settle(app, listener, run)

    assert "from notification" in render(view(app))


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
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app, _listener, _run = await start_turn(app, "hello")
    stale = ConversationNotificationMsg(
        notification=MessageAppended(message=Message(role=ROLE_ASSISTANT, content="stale")),
        turn=app.turn - 1,
    )

    app, commands = await send(app, stale)
    assert len(commands) == 1, "the listener is re-armed on the current channel"
    assert "stale" not in render(view(app)), "a notification from a replaced turn is dropped"


@pytest.mark.asyncio
async def test_default_printer_commits_messages_to_terminal_output() -> None:
    output = io.StringIO()
    console = Console(width=80, file=output, force_terminal=False, color_system=None)
    fake = FakeConversation(
        script=[
            MessageAppended(message=Message(role=_USER, content="hi")),
            MessageAppended(message=Message(role=ROLE_ASSISTANT, content="from notification")),
        ]
    )
    program: Program[App, Msg] = Program(model=new_app(fake), update=update, view=view, console=console, input_fd=-1)
    program.send(WindowSizeMsg(width=80, height=24))
    await program.step()
    for character in "hi":
        program.send(KeyMsg(key=character))
    program.send(KeyMsg(key="enter"))
    await _pump(program)

    assert f"{_USER_GLYPH} hi" in output.getvalue(), output.getvalue()
    assert "from notification" in output.getvalue(), output.getvalue()
    await program.shutdown()


@pytest.mark.asyncio
async def test_new_listener_cancels_the_one_it_replaces() -> None:
    """A replaced turn's listener must not linger on the channel it was armed for."""
    console = Console(width=80, file=io.StringIO(), force_terminal=False, color_system=None)
    app = await resize(new_app(FakeConversation()), 80, 24)
    program: Program[App, Msg] = Program(model=app, update=update, view=view, console=console)
    message = notification(app, MessageAppended(message=Message(role=ROLE_ASSISTANT, content="one")))

    await program.dispatch(message)
    first = program.listener
    assert first is not None
    await program.dispatch(message)
    assert program.listener is not first, "each listener replaces the previous one"
    with contextlib.suppress(asyncio.CancelledError):
        await first
    assert first.cancelled(), "the listener of the previous turn is cancelled"
    await program.shutdown()


async def _pump(program: Program[App, Msg], rounds: int = 200) -> None:
    """Drive the loop until nothing is left to deliver."""
    for _ in range(rounds):
        await asyncio.sleep(0)
        while not program.queue.empty():
            await program.step()


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
                    tool_calls=(ToolCall(name="apply_patch", input='{"path": "tui/view.py"}'),),
                )
            ),
        ]
    )
    app = await resize(new_app(fake), 80, 24)
    app, listener, run = await start_turn(app, "inspect")
    app, _ = await settle(app, listener, run)

    rendered = render(view(app))
    assert "● Read 2 files" in rendered and "● Edited tui/view.py" in rendered
    assert "private reasoning" not in rendered, "reasoning stays collapsed by default"
    assert '{"path"' not in rendered, "raw tool inputs stay hidden"

    app, _ = await press(app, "ctrl+o")
    latest = render(view(app))
    assert "tui/view.py" in latest and "tui/app.py" not in latest, "only the latest group expands"

    app, _ = await press(app, "alt+o")
    expanded = render(view(app))
    assert "tui/app.py" in expanded and "tui/view.py" in expanded
    assert expanded.index("● Read") < expanded.index("tui/app.py") < expanded.index("● Edited"), (
        "expanded details stay below their tool call"
    )

    app, _ = await press(app, "ctrl+r")
    latest_thinking = render(view(app))
    assert "patch reasoning" in latest_thinking and "private reasoning" not in latest_thinking

    app, _ = await press(app, "alt+r")
    all_thinking = render(view(app))
    assert "patch reasoning" in all_thinking and "private reasoning" in all_thinking


@pytest.mark.asyncio
async def test_terminal_key_decoding_matches_bubble_tea_names() -> None:
    """The raw-mode decoder produces the names the features switch on."""
    decoder = KeyDecoder()
    assert decoder.feed(b"hi") == ["h", "i"]
    assert decoder.feed(b"\r") == ["enter"]
    assert decoder.feed(b"\t") == ["tab"]
    assert decoder.feed(b"\x03") == ["ctrl+c"]
    assert decoder.feed(b"\n") == ["ctrl+j"]
    assert decoder.feed(b"\x1b[A\x1b[B") == ["up", "down"]
    assert decoder.feed(b"\x1bo") == ["alt+o"]
    assert decoder.feed(b"\x1b[13;2u") == ["shift+enter"]
    assert decoder.feed(b"\x1b") == [], "a lone escape waits for the timeout"
    assert decoder.flush() == ["esc"]
    assert decoder.feed(b"\x1b[1;5C") == [], "an unknown sequence stays pending"
    assert decoder.flush() == []


@pytest.mark.asyncio
async def test_page_keys_do_not_replace_terminal_scrollback() -> None:
    fake = FakeConversation(extra_messages=30)
    app = await resize(new_app(fake), 80, 15)
    app, listener, run = await start_turn(app, "hello")
    app, _ = await settle(app, listener, run)

    before = render(view(app))
    app, _ = await press(app, "pgup")
    assert render(view(app)) == before, "page keys belong to the terminal"
    app, _ = await press(app, "pgdown")
    assert render(view(app)) == before


@pytest.mark.asyncio
async def test_stream_chunk_replaces_the_streaming_message() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app, _listener, _run = await start_turn(app, "hello")

    stream = StreamChunkReceived(message=Message(role=ROLE_ASSISTANT, content="streaming"))
    app, commands = await send(app, notification(app, stream))
    assert len(commands) == 1
    assert "streaming" in render(view(app))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_instructions_command_displays_loaded_sources() -> None:
    printed: list[str] = []
    info = StartupInfo(model_name="test-model", instruction_paths=("/repo/AGENTS.md", "/repo/pkg/CLAUDE.md"))
    app = await resize(new(FakeConversation(), info, with_output_printer(recording_printer(printed))), 80, 24)
    app = await type_text(app, "/instructions")
    app, _ = await press(app, "enter")

    output = "\n".join(printed)
    assert "Loaded instruction sources" in output
    assert "/repo/AGENTS.md" in output and "/repo/pkg/CLAUDE.md" in output


@pytest.mark.asyncio
async def test_permissions_mode_command_rejects_invalid_mode() -> None:
    fake = FakeConversation(permission_error=ValueError("invalid permission mode: root"))
    info = StartupInfo(model_name="test-model", permission_mode="ask")
    app = await resize(new_app(fake, info), 80, 24)
    app = await type_text(app, "/permissions mode root")
    app, _ = await press(app, "enter")

    rendered = render(view(app))
    assert "Permissions failed: invalid permission mode: root" in rendered
    assert "mode:root" not in rendered, "the mode stays as it was"
    assert fake.permission_mode() == "root", "the port was asked, and refused"


@pytest.mark.asyncio
async def test_permissions_mode_keeps_displayed_model() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake, StartupInfo(model_name="test-model", permission_mode="ask")), 80, 24)
    app = await type_text(app, "/permissions mode plan")
    app, _ = await press(app, "enter")

    assert "plan · test-model · tools on" in render(view(app))


@pytest.mark.asyncio
async def test_attach_command_routes_through_the_attachments_feature() -> None:
    fake = FakeConversation()
    app = await resize(new_app(fake), 80, 24)
    app = await type_text(app, "/attach notes.md")
    app, commands = await press(app, "enter")

    assert len(commands) == 1, "the attach command starts work"
    message = await commands[0]()
    assert message is not None
    app, _ = await send(app, message)
    rendered = render(view(app))
    assert "Attachments: notes.md" in rendered
    assert "Attached notes.md (text/plain)" in rendered
    assert fake.attached_paths == ["notes.md"]


@pytest.mark.asyncio
async def test_mcp_commands_list_and_add_server() -> None:
    printed: list[str] = []
    fake = FakeConversation(mcp_servers=[MCPServerSummary(name="files", tools=("read_remote",))])
    app = new(
        fake,
        StartupInfo(model_name="test-model"),
        with_output_printer(recording_printer(printed)),
    )
    app = await resize(app, 80, 24)

    app = await type_text(app, "/mcp list")
    app, _ = await press(app, "enter")
    assert any("files  read_remote" in item for item in printed), printed

    app = await type_text(app, "/mcp add local helper --stdio")
    app, commands = await press(app, "enter")
    assert len(commands) == 1, "an MCP change runs asynchronously"

    message = await commands[0]()
    assert message is not None
    app, _ = await send(app, message)
    assert fake.mcp_added == ("local", "helper", ["--stdio"])
    assert "Added MCP server local" in render(view(app))


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


async def open_approval_in(pilot: Pilot[None]) -> None:
    """Submit a prompt whose turn asks for approval, and wait for the prompt.

    ``Pilot`` is parameterised by the application's exit value, which this
    application does not have.
    """
    await pilot.press("g", "o", "enter")
    await pilot.pause()


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
