"""The modal tool-approval prompt.

The dialog is this feature's keyboard surface. It maps the terminal's key names
onto the vocabulary the model switches on, swallows every other key so the
composer behind it cannot be edited by accident, and reports the two things the
application owns: the answer, and a request to leave the prompt.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Static

from super_agent.tui.approval.model import Decision, Model

__all__ = ["CANCEL_KEYS", "KEY_NAMES", "ApprovalDialog"]

#: The terminal's key names, mapped onto the keys the model handles.
KEY_NAMES: dict[str, str] = {
    "up": "up",
    "k": "k",
    "down": "down",
    "j": "j",
    "enter": "enter",
    "1": "1",
    "y": "y",
    "2": "2",
    "a": "a",
    "3": "3",
    "n": "n",
}

#: The keys that leave the prompt to the application, which owns the turn.
CANCEL_KEYS: frozenset[str] = frozenset({"escape", "ctrl+c"})


class ApprovalDialog(ModalScreen[None]):
    """One pending request, its selection, and the decision the runtime receives.

    The dialog stays open once a decision is submitted: the model's latch is what
    keeps a repeated keypress from answering the next request, and the view has to
    stay put for that latch to be visible.

    A centred card rather than a full-screen pane: the prompt is one decision, and
    the transcript behind it stays readable. Textual sizes a bare container at
    ``1fr x 1fr``, so the box states its own size — it hugs the request, wraps
    within the screen, and is centred by alignment rather than by arithmetic.
    """

    DEFAULT_CSS = """
    ApprovalDialog { align: center middle; background: $background 70%; }
    ApprovalDialog > Vertical {
        width: auto;
        height: auto;
        max-width: 90%;
        max-height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    ApprovalDialog Static { width: 1fr; height: auto; }
    """

    class Answered(Message):
        """The user answered the pending request."""

        def __init__(self, decision: Decision) -> None:
            super().__init__()
            self.decision = decision

    class Cancelled(Message):
        """The user left the prompt without answering."""

    def __init__(self, model: Model, *, cwd: str) -> None:
        super().__init__()
        self.model = model
        self.cwd = cwd

    def compose(self) -> ComposeResult:
        with Vertical(id="approval-dialog"):
            yield Static(self.view(), id="approval-request", markup=False)

    def view(self) -> Text:
        """The rendered request, read straight off the model it belongs to."""
        return self.model.view(self.cwd)

    async def on_key(self, event: events.Key) -> None:
        """Answer, move the selection, or leave. Every other key stops here."""
        event.prevent_default()
        event.stop()
        if event.key in CANCEL_KEYS:
            self.post_message(self.Cancelled())
            return
        name = KEY_NAMES.get(event.key)
        if name is None:
            return
        _, decision, submitted = self.model.update(name)
        self.query_one("#approval-request", Static).update(self.view())
        if submitted:
            self.post_message(self.Answered(decision))
