"""The Textual application shell for the terminal interface."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence
from typing import ClassVar, cast

from rich.text import Text
from textual import events
from textual.app import App as TextualApp, ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import VerticalScroll
from textual.message import Message as TextualMessage
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea

from super_agent.tui import actions, runtime, statusline, theme
from super_agent.tui.app import App as AppModel, Msg
from super_agent.tui.approval import ApprovalDialog
from super_agent.tui.composer import Composer
from super_agent.tui.editor import edit_draft
from super_agent.tui.pager import PagerScreen
from super_agent.tui.transcript.screen import TranscriptScreen
from super_agent.tui.update import update

__all__ = ["Application"]

#: The keys the composer owns while it has focus.
_COMPOSER_ACTIONS = frozenset({"submit", "newline", "complete_or_queue", "vertical_key"})

#: The keys the transcript viewport owns.
_TRANSCRIPT_ACTIONS = frozenset({"page_up", "page_down"})

#: Two empty-draft presses inside this window are a deliberate quit gesture.
_DOUBLE_CTRL_C_SECONDS = 1.0

#: The actions that stand aside while a modal owns the keyboard. An application
#: binding is checked before the focused widget, so one that ran regardless would
#: take the key from the overlay that is supposed to be answering it. Cancelling
#: is deliberately absent: the escape hatch stays open under every overlay.
_MODAL_ACTIONS = (
    _COMPOSER_ACTIONS
    | _TRANSCRIPT_ACTIONS
    | {
        "model_key",
        "help",
        "help_or_type",
        "clear_status",
        "open_pager",
        "edit_draft",
    }
)

#: The help text, in one place: ``F1``, ``?``, and ``/help`` all land on it.
_HELP_TEXT = (
    "Commands & shortcuts\n\n"
    "Enter  submit / steer\nTab  queue while running\nCtrl+J  newline\n"
    "PgUp/PgDn  scroll transcript\nCtrl+O  toggle tools\nCtrl+R  toggle reasoning\n"
    "Ctrl+T  transcript pager\nCtrl+G  edit draft in $VISUAL/$EDITOR\n"
    "Ctrl+C  clear / cancel / double quit\nEsc  cancel / clear"
)

#: The keys that close help: the viewer's own pair, and the two that open it.
#: ``update.updateKey`` gives ``?`` and ``esc`` the same meaning.
_HELP_CLOSE_KEYS = frozenset({"escape", "q", "f1", "question_mark"})


class _ModelMessage(TextualMessage):
    """A result produced by one legacy feature command."""

    def __init__(self, value: Msg) -> None:
        super().__init__()
        self.value = value


class OutputScreen(ModalScreen[None]):
    """Scrollable command output owned by the alternate-screen application."""

    DEFAULT_CSS = """
    OutputScreen { align: center middle; background: $background 70%; }
    OutputScreen > VerticalScroll {
        width: 90%; height: 85%; border: round $accent; background: $surface; padding: 1 2;
    }
    OutputScreen Static { width: 1fr; height: auto; }
    """
    BINDINGS: ClassVar[list[BindingType]] = [("escape", "close", "Close"), ("q", "close", "Close")]

    def __init__(self, content: str) -> None:
        super().__init__()
        self.content = content

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static(self.content, markup=False)

    def action_close(self) -> None:
        self.dismiss(None)


class HelpOverlay(OutputScreen):
    """The help text, in the same frame as any other long output.

    It differs from the viewer in one way: closing it reports to the root instead
    of dismissing itself. The model's ``showHelp`` flag is what owns whether this
    surface exists, so the two can never disagree — the root pushes it when the
    flag turns true and dismisses it when the flag turns false.
    """

    class Dismissed(TextualMessage):
        """The user asked for help to close."""

    def __init__(self) -> None:
        super().__init__(_HELP_TEXT)

    async def on_key(self, event: events.Key) -> None:
        """Own the keyboard: close on help's keys, and swallow everything else."""
        event.prevent_default()
        event.stop()
        if event.key in _HELP_CLOSE_KEYS:
            self.action_close()

    def action_close(self) -> None:
        """Report the close rather than dismissing: the flag owns the surface."""
        self.post_message(self.Dismissed())


class Application(TextualApp[None]):
    """A full-screen terminal application around the framework-neutral TUI model."""

    CSS_PATH = "theme.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "submit", "Submit", priority=True),
        Binding("ctrl+m", "submit", "Submit", priority=True),
        Binding("ctrl+j", "newline", "Newline", priority=True),
        Binding("shift+enter", "newline", "Newline", priority=True),
        Binding("alt+enter", "newline", "Newline", priority=True),
        Binding("tab", "complete_or_queue", "Complete / queue", priority=True),
        Binding("up", "vertical_key('up')", "Previous", priority=True),
        Binding("down", "vertical_key('down')", "Next", priority=True),
        Binding("ctrl+c", "cancel_or_quit", "Cancel / quit", priority=True),
        ("escape", "cancel_or_clear", "Cancel / clear"),
        Binding("pageup", "page_up", "Page up", priority=True),
        Binding("pagedown", "page_down", "Page down", priority=True),
        Binding("question_mark", "help_or_type", "Help", priority=True, show=False),
        ("ctrl+o", "model_key('ctrl+o')", "Toggle tools"),
        ("alt+o", "model_key('alt+o')", "Toggle all tools"),
        ("ctrl+r", "model_key('ctrl+r')", "Toggle reasoning"),
        ("alt+r", "model_key('alt+r')", "Toggle all reasoning"),
        ("ctrl+y", "model_key('ctrl+y')", "Copy code"),
        ("ctrl+u", "model_key('ctrl+u')", "Clear input"),
        ("ctrl+l", "clear_status", "Clear status"),
        ("ctrl+t", "open_pager", "Transcript pager"),
        ("ctrl+g", "edit_draft", "Edit draft"),
        ("f1", "help", "Help"),
    ]

    def __init__(self, model: AppModel) -> None:
        # ``ansi_color`` is stated before the theme below can state it. The
        # framework chooses its ``NO_COLOR`` filter in this call, reading
        # ``native_ansi_color`` from the theme that is active *then* -- still the
        # default one. Left to infer it, the app is handed the monochrome filter,
        # whose black-and-white pass cannot resolve an ANSI colour *name* and so
        # paints every role, default text included, pure black.
        super().__init__(ansi_color=True)
        self.register_theme(theme.THEME)
        self.theme = theme.NAME
        self.model = model
        self._tasks: set[asyncio.Task[None]] = set()
        self._listener: asyncio.Task[None] | None = None
        self._unread = 0
        self._transcript_fingerprint: object = None
        self._approval_dialog: ApprovalDialog | None = None
        self._help_overlay: HelpOverlay | None = None
        self._quit_armed_until = 0.0
        self.model.printOutput = self._output_printer

    def compose(self) -> ComposeResult:
        # ``#suggestions`` sits before the composer and floats out of the flow,
        # so the palette lands on the composer's top edge without borrowing the
        # transcript's height; ``#queue`` is the composer area's own rows.
        yield TranscriptScreen(id="transcript")
        yield Static(id="unread")
        yield Static(id="queue", markup=False)
        yield Static(id="suggestions", markup=False)
        yield Composer(
            placeholder="\u276f Ask Super Agent to do anything",
            id="composer",
            soft_wrap=True,
            show_line_numbers=False,
        )
        yield Static(id="status", markup=False)

    async def run_terminal(self) -> None:
        """Run with mouse reporting, which is what lets the wheel reach the interface.

        The terminal's own alternate-scroll turns the wheel into arrow keys while
        reporting is off, and an arrow key is the composer's history; reporting is
        what makes a wheel notch scroll the surface under the pointer instead.
        """
        await self.run_async(mouse=True)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Which application bindings may claim a key right now.

        Two rules. A modal owns the keyboard while it is open, because a binding
        is checked before the focused widget and would otherwise take the key
        from the overlay answering it. And ``?`` is help only while there is no
        draft to type it into; a binding is checked before the editor, so
        declining is what lets the character through.
        """
        if isinstance(self.screen, ModalScreen) and action in _MODAL_ACTIONS:
            return False
        if action == "help_or_type":
            return self.model.composer.draft() == ""
        return True

    async def on_mount(self) -> None:
        self.query_one("#composer", TextArea).focus()
        await self._dispatch(runtime.WindowSizeMsg(width=self.size.width, height=self.size.height))
        self._start(self.model.init())

    async def on_unmount(self) -> None:
        pending = list(self._tasks)
        if self._listener is not None and self._listener not in pending:
            pending.append(self._listener)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def on_resize(self, event: events.Resize) -> None:
        await self._dispatch(runtime.WindowSizeMsg(width=event.size.width, height=event.size.height))

    async def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "composer":
            return
        self._sync_editor_to_model()
        self._render_suggestions()

    async def on__model_message(self, message: _ModelMessage) -> None:
        await self._dispatch(message.value)

    async def action_cancel_or_quit(self) -> None:
        selection = self.screen.get_selected_text()
        if selection and (copy := actions.copyCommand(self.model, selection)) is not None:
            self.screen.clear_selection()
            self._start((copy,))
            return
        self._sync_editor_to_model()
        if self.model.composer.draft() != "":
            self._quit_armed_until = 0.0
            await self._model_key("ctrl+u")
            return
        if self.model.approval.active() or self.model.cancellation is not None:
            self._quit_armed_until = 0.0
            await self._model_key("ctrl+c")
            return
        now = time.monotonic()
        if now <= self._quit_armed_until:
            self._quit_armed_until = 0.0
            await self._model_key("ctrl+c")
            return
        self._quit_armed_until = now + _DOUBLE_CTRL_C_SECONDS
        self.model.err = ""
        self.model.status = "Press Ctrl+C again to quit"
        await self._render_model()

    async def action_cancel_or_clear(self) -> None:
        await self._model_key("esc")

    async def on_approval_dialog_answered(self, message: ApprovalDialog.Answered) -> None:
        self.model.transcript.clear_streaming()
        self.model.approvals.put(message.decision)

    async def on_approval_dialog_cancelled(self, _message: ApprovalDialog.Cancelled) -> None:
        await self._model_key("esc")

    async def on_help_overlay_dismissed(self, _message: HelpOverlay.Dismissed) -> None:
        """Closing help is the model's key for it, so flag and screen clear together."""
        await self._model_key("esc")

    async def action_submit(self) -> None:
        if self.query_one("#composer", TextArea).has_focus:
            await self._model_key("enter")

    def action_newline(self) -> None:
        composer = self.query_one("#composer", TextArea)
        if composer.has_focus:
            composer.insert("\n")

    async def action_complete_or_queue(self) -> None:
        if self.query_one("#composer", TextArea).has_focus:
            await self._model_key("tab")

    async def action_vertical_key(self, key: str) -> None:
        composer = self.query_one("#composer", TextArea)
        if not composer.has_focus:
            return
        if self.model.approval.active() or self._should_route_vertical_key():
            await self._model_key(key)
        elif key == "up":
            composer.action_cursor_up()
        else:
            composer.action_cursor_down()

    def action_page_up(self) -> None:
        self.query_one("#transcript", TranscriptScreen).scroll_page_up(animate=False)

    def action_page_down(self) -> None:
        self.query_one("#transcript", TranscriptScreen).scroll_page_down(animate=False)

    def on_mouse_scroll_up(self, _event: events.MouseScrollUp) -> None:
        """The wheel's fallback: the surface under the pointer had nothing to scroll."""
        self._scroll_transcript(rows=-self.scroll_sensitivity_y)

    def on_mouse_scroll_down(self, _event: events.MouseScrollDown) -> None:
        self._scroll_transcript(rows=self.scroll_sensitivity_y)

    def _scroll_transcript(self, *, rows: float) -> None:
        """Scroll the transcript, unless an overlay is the surface holding the pointer.

        An overlay is drawn over the transcript, so a wheel notch that reached
        here while one is open is meant for it, and nothing behind it may move.
        """
        if isinstance(self.screen, ModalScreen):
            return
        self.query_one("#transcript", TranscriptScreen).scroll_by(rows)

    async def action_model_key(self, key: str) -> None:
        await self._model_key(key)

    async def action_help_or_type(self) -> None:
        """``?`` opens help; ``check_action`` sends it to the editor otherwise."""
        await self.action_help()

    async def action_clear_status(self) -> None:
        """Drop the transient status line and go back to the latest content."""
        self._unread = 0
        await self._dispatch(runtime.ClearScreenMsg())

    async def action_help(self) -> None:
        """Raise help.

        The flag is set here rather than by dispatching ``?``: ``F1`` opens help
        whatever the draft holds, and the character would type itself into a
        non-empty one. ``?`` reaches this action only when the draft is empty,
        because ``check_action`` leaves the character to the editor otherwise.
        """
        self.model.showHelp = True
        self.model.status = ""
        await self._render_model()

    async def action_open_pager(self) -> None:
        """Hand the whole transcript to the pager.

        The pager is given the composed text rather than the live viewport: it
        reads a transcript while it is no longer being appended to, and reaching
        into the transcript feature's widgets from here would make the two
        features one.
        """
        self.push_screen(PagerScreen(self.model.transcript.view()))

    async def action_edit_draft(self) -> None:
        """Hand the draft to ``$VISUAL``/``$EDITOR`` and take back what it saves.

        The draft reaches the editor and comes back through the same two syncs
        every other key uses, so the composer's history, cursor, and selection
        see one edit rather than a replacement.
        """
        self._sync_editor_to_model()
        edited = await edit_draft(self, self.model.composer.value)
        if edited is None:
            return
        self.model.composer.value = edited
        self._sync_model_to_editor()
        self.query_one("#composer", TextArea).focus()

    async def _model_key(self, key: str) -> None:
        self._sync_editor_to_model()
        await self._dispatch(runtime.KeyMsg(key=key))

    async def _dispatch(self, message: Msg) -> None:
        if isinstance(message, runtime.QuitMsg):
            self.exit()
            return
        if isinstance(message, runtime.ClearScreenMsg):
            self.model.err = ""
            self.model.status = ""
            self.query_one("#transcript", TranscriptScreen).follow()
        self.model, commands = await update(self.model, message)
        await self._render_model()
        self._start(commands)

    async def _render_model(self) -> None:
        pane = self.query_one("#transcript", TranscriptScreen)
        was_following = pane.following
        fingerprint = (
            tuple(self.model.transcript.messages),
            self.model.transcript.streaming,
            self.model.transcript.busy,
            self.model.transcript.expandLatestTools,
            self.model.transcript.expandAllTools,
            self.model.transcript.expandLatestThink,
            self.model.transcript.expandAllThink,
        )
        transcript_changed = fingerprint != self._transcript_fingerprint
        self._transcript_fingerprint = fingerprint
        await pane.sync(self.model.transcript)
        await self._sync_approval_dialog()
        await self._sync_help_overlay()
        self._render_status()
        self._sync_model_to_editor()
        self._render_queue()
        self._render_suggestions()
        if was_following:
            pane.follow()
            self._unread = 0
        elif transcript_changed:
            self._unread += 1
        self._render_unread()

    def _render_status(self) -> None:
        """The bottom row: the error, the transient status, or the configured items.

        The three are exclusive and ordered by precedence, as ``docs/tui.md``
        specifies: a command's error line takes the row, its status line takes it
        next, and otherwise the row is whatever ``tui.status_line`` asks for. An
        empty row is hidden rather than drawn blank, which is what lets ``null``
        return its height to the transcript.
        """
        widget = self.query_one("#status", Static)
        if self.model.err:
            rendered = Text("error: " + self.model.err, style=self.model.styles.error)
        elif self.model.status:
            rendered = Text(self.model.status, style=self.model.styles.accent)
        else:
            rendered = statusline.compose(self.model)
        widget.update(rendered)
        widget.display = bool(rendered.plain)

    def _render_queue(self) -> None:
        """The composer area's queued follow-ups, above the editor.

        The preview is the composer model's: it holds the queue, and it is the
        feature that knows how many rows a preview is worth.
        """
        widget = self.query_one("#queue", Static)
        rendered = self.model.composer.queueView()
        widget.update(rendered)
        widget.display = bool(rendered.plain)

    def _render_suggestions(self) -> None:
        widget = self.query_one("#suggestions", Static)
        rendered = self.model.composer.paletteView()
        widget.update(rendered)
        widget.display = bool(rendered.plain)

    async def _sync_approval_dialog(self) -> None:
        if self.model.approval.active() and self._approval_dialog is None:
            dialog = ApprovalDialog(self.model.approval, cwd=self.model.info.cwd)
            self._approval_dialog = dialog
            await self.push_screen(dialog)
        elif not self.model.approval.active() and self._approval_dialog is not None:
            dialog, self._approval_dialog = self._approval_dialog, None
            await dialog.dismiss(None)

    async def _sync_help_overlay(self) -> None:
        """Help follows the model's flag, the way the approval menu does.

        The flag is the only owner of this surface: ``/help`` raises it without
        touching a widget, and the overlay's own dismissal clears it through the
        model, which is what lets the next keystroke reach the composer again.
        """
        if self.model.showHelp and self._help_overlay is None:
            overlay = HelpOverlay()
            self._help_overlay = overlay
            await self.push_screen(overlay)
        elif not self.model.showHelp and self._help_overlay is not None:
            overlay, self._help_overlay = self._help_overlay, None
            await overlay.dismiss(None)

    def _render_unread(self) -> None:
        widget = self.query_one("#unread", Static)
        widget.update(f"↓ {self._unread} new updates" if self._unread else "")
        widget.display = self._unread > 0

    def _sync_editor_to_model(self) -> None:
        editor = self.query_one("#composer", TextArea)
        self.model.composer.value = editor.text
        row, column = editor.selection.end
        lines = editor.text.splitlines(keepends=True)
        self.model.composer.cursor = sum(len(line) for line in lines[:row]) + column
        self.model.composer.historyIndex = min(self.model.composer.historyIndex, len(self.model.composer.history))

    def _sync_model_to_editor(self) -> None:
        editor = self.query_one("#composer", TextArea)
        if editor.text != self.model.composer.value:
            editor.load_text(self.model.composer.value)
            editor.move_cursor((len(editor.document.lines) - 1, len(editor.document.lines[-1])))

    def _should_route_vertical_key(self) -> bool:
        value = self.query_one("#composer", TextArea).text
        return bool(self.model.composer.matches()) or "\n" not in value

    def _start(self, commands: Sequence[runtime.Command[Msg]]) -> None:
        for command in commands:
            if isinstance(command, runtime.Listener):
                if self._listener is not None and not self._listener.done():
                    self._listener.cancel()
                self._listener = self._spawn(cast("runtime.Listener[Msg]", command))
            else:
                self._spawn(command)

    def _spawn(self, command: runtime.Command[Msg]) -> asyncio.Task[None]:
        async def run() -> None:
            with contextlib.suppress(asyncio.CancelledError):
                value = await command()
                if value is not None:
                    self.post_message(_ModelMessage(value))

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _output_printer(self, content: str) -> runtime.Command[Msg] | None:
        if not content.strip():
            return None

        async def show() -> None:
            self.call_later(self.push_screen, OutputScreen(content))

        return show
