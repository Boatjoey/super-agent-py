"""Retained transcript widgets and viewport state."""

from __future__ import annotations

from typing import ClassVar

from rich.text import Text
from textual.binding import BindingType
from textual.containers import VerticalScroll
from textual.widgets import Static

from super_agent.tui.transcript.model import Message, Model

__all__ = ["TranscriptScreen"]


class TranscriptScreen(VerticalScroll):
    """A viewport that retains one widget per committed message.

    The viewport carries its own follow state, because only the widget sees every
    scroll: a watcher on ``scroll_y`` catches the wheel and the page keys, and
    catches them as they happen, before anything can decide to pull the view back
    down. ``following`` is sticky in the other direction — content growing does
    not move the viewport — so only a scroll that moved it settles the flag.
    """

    #: A click selects and scrolls; it does not take the keyboard. A drag that
    #: selects a line must not cost the next keypress of the draft, and the
    #: composer is where every key is meant to land.
    FOCUS_ON_CLICK = False

    can_focus = True
    BINDINGS: ClassVar[list[BindingType]] = [
        ("pageup", "scroll_page_up", "Page up"),
        ("pagedown", "scroll_page_down", "Page down"),
        ("home", "scroll_home", "First message"),
        ("end", "scroll_end", "Latest message"),
    ]

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._welcome = Static(classes="transcript-welcome", markup=False)
        self._welcome_tip = Static(
            Text("Tip: Type /help for commands and keyboard shortcuts."),
            classes="welcome-tip",
        )
        self._messages: list[Static] = []
        self._rendered: list[tuple[Message, bool, bool, int] | None] = []
        self._stream = Static(classes="transcript-stream", markup=False)
        self._stream_value: tuple[Message | None, bool, int] | None = None
        self._welcome_value = ""
        #: Whether the viewport follows the latest content. The shell reads it
        #: before it commits new content: a viewport that was following moves to
        #: the end, and one that was not stays where the user left it.
        self.following = True

    async def on_mount(self) -> None:
        await self.mount(self._welcome, self._welcome_tip, self._stream)

    def watch_scroll_y(self, old_value: float, new_value: float) -> None:
        """Settle ``following`` on every scroll, whichever way it was asked for."""
        super().watch_scroll_y(old_value, new_value)
        self.following = self.is_vertical_scroll_end

    def follow(self) -> None:
        """Pin the viewport to the latest content, and keep following it."""
        self.following = True
        self.scroll_end(animate=False)

    def scroll_by(self, rows: float) -> None:
        """Move the viewport by ``rows``, the distance one wheel notch travels."""
        self.scroll_to(y=self.scroll_y + rows, animate=False, immediate=True)

    def _take_width(self, model: Model) -> None:
        """Tell the model the width it is rendered into.

        The pane's content width, not the terminal's: markdown is measured
        against the box it lands in, and the padding is already subtracted from
        this number. Measuring against the terminal instead is what left wrapped
        prose centred for a box several columns wider than the one it appeared
        in.
        """
        width = self.content_size.width
        if width > 0 and width != model.width:
            model.set_width(width)

    async def sync(self, model: Model) -> None:
        """Reconcile changed blocks without replacing the viewport."""
        self._take_width(model)
        if model.welcome != self._welcome_value:
            self._welcome.update(model.welcome)
            self._welcome_value = model.welcome
        while len(self._messages) > len(model.messages):
            widget = self._messages.pop()
            self._rendered.pop()
            await widget.remove()
        while len(self._messages) < len(model.messages):
            widget = Static(classes="transcript-message", markup=False)
            self._messages.append(widget)
            self._rendered.append(None)
            await self.mount(widget, before=self._stream)

        latest_tool = -1
        latest_thinking = -1
        for index, message in enumerate(model.messages):
            if message.tool_calls:
                latest_tool = index
            if message.role == "assistant" and message.reasoning_content.strip():
                latest_thinking = index
        for index, message in enumerate(model.messages):
            tools_expanded = model.expandAllTools or (model.expandLatestTools and index == latest_tool)
            thinking_expanded = model.expandAllThink or (model.expandLatestThink and index == latest_thinking)
            signature = (message, tools_expanded, thinking_expanded, model.width)
            if signature != self._rendered[index]:
                self._messages[index].update(model.renderCommitted(message, tools_expanded, thinking_expanded))
                self._rendered[index] = signature

        stream_value = (model.streaming, model.busy, model.width)
        if stream_value != self._stream_value:
            streaming = model.streamingView()
            self._stream.update(streaming)
            self._stream.display = bool(streaming.plain)
            self._stream_value = stream_value
