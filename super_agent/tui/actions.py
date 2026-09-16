"""Clipboard, code-block extraction, and turn cancellation.

The clipboard write is the one child process this package starts, so it runs as a
command: copying a large block must never stall rendering.

Two deliberate choices:

* The clipboard write prefers OSC 52 when stdout is a terminal — it is the only
  encoding that survives SSH and tmux — and falls back to ``pyperclip`` when it
  is not.
* ``cancelRun`` is a coroutine, because cancelling the turn is an ``await`` on
  the turn port rather than a synchronous call.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import sys
from typing import TYPE_CHECKING

from super_agent.tui import runtime
from super_agent.tui.transcript import extract_code_blocks as extract_code_blocks

if TYPE_CHECKING:
    from super_agent.tui.app import App, Msg

__all__ = [
    "ClipboardDone",
    "cancelRun",
    "copyCommand",
    "defaultClipboardWrite",
    "extract_code_blocks",
    "finishCopy",
    "osc52",
]


@dataclasses.dataclass(frozen=True, slots=True)
class ClipboardDone:
    """The outcome of an asynchronous clipboard write."""

    lines: int = 0
    err: BaseException | None = None


def osc52(text: str) -> str:
    """The OSC 52 sequence that puts ``text`` on the terminal's clipboard."""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"\x1b]52;c;{encoded}\x07"


def defaultClipboardWrite(text: str) -> None:
    """Write ``text`` to the clipboard, off the update loop.

    OSC 52 reaches the terminal's own clipboard over SSH and inside tmux, where
    no X11 or Wayland clipboard tool exists. It only means anything when stdout
    is a terminal, so anything else goes through ``pyperclip``.
    """
    if _stdout_is_terminal():
        sys.stdout.write(osc52(text))
        sys.stdout.flush()
        return
    import pyperclip

    pyperclip.copy(text)


def _stdout_is_terminal() -> bool:
    """Whether stdout is a terminal, tolerating a stream that refuses the question."""
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def copyCommand(app: App, text: str) -> runtime.Command[Msg] | None:
    """The command that writes ``text`` to the clipboard.

    The native clipboard tools are child processes, so the write happens in a
    worker thread and its result comes back as a :class:`ClipboardDone`.
    """
    if not text.strip():
        return None
    write = app.writeClipboard
    lines = text.count("\n") + 1

    async def run() -> ClipboardDone:
        try:
            await asyncio.to_thread(write, text)
        except Exception as err:
            return ClipboardDone(lines=lines, err=err)
        return ClipboardDone(lines=lines)

    return run


def finishCopy(app: App, message: ClipboardDone) -> App:
    """Report the outcome of an asynchronous clipboard write."""
    if message.err is not None:
        app.err = f"Failed to copy: {message.err}"
        app.status = ""
        return app
    app.err = ""
    label = "line" if message.lines == 1 else "lines"
    app.status = f"Copied {message.lines} {label}"
    return app


async def cancelRun(app: App, clearQueue: bool) -> App:
    """Cancel the turn in flight, optionally abandoning everything queued for it.

    A steering cancellation keeps the queue: the user is mid-thought rather than
    abandoning the work.
    """
    if app.approval.active():
        err = await app.turnPort.cancel()
        if err is not None:
            app.err = str(err)
    if app.cancellation is not None:
        app.cancellation.cancel()
    if clearQueue:
        app.composer.clear_queue()
        app.status = "Turn canceled"
    return app
