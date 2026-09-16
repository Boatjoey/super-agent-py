"""The Elm/MVU runtime the TUI runs on.

Go gets this from Bubble Tea: a message loop, commands that run off the update
thread and post their result back, a raw-mode key decoder, and a renderer. Python
has no equivalent, so the loop is written here and kept deliberately small:

    message = await queue.get()
    model, commands = await update(model, message)
    render(model)
    for command in commands: start(command)

Three things have no Go counterpart and are worth naming:

* :class:`Listener` marks a command that waits on a channel until its turn ends.
  Go leaves the goroutine blocked; here the program keeps the task so a new turn
  can cancel the previous listener instead of leaking it.
* :class:`Scrollback` is ``tea.Println``: content committed above the live view
  rather than into it.
* The console is module state (:func:`active_console`) because ``tea.Println``
  reaches the running program implicitly. Exactly one program runs per process.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
import select
import signal
import sys
import termios
import tty
from collections.abc import Awaitable, Callable, Generator, Sequence
from typing import Any, Final, cast

from rich.console import Console, RenderableType
from rich.live import Live

__all__ = [
    "ClearScreen",
    "ClearScreenMsg",
    "Command",
    "KeyDecoder",
    "KeyMsg",
    "Listener",
    "Program",
    "Quit",
    "QuitMsg",
    "Scrollback",
    "WindowSizeMsg",
    "active_console",
    "batch",
    "read_keys",
]

type Command[M] = Callable[[], Awaitable[M | None]]
"""A unit of work the loop runs: an awaitable producing at most one message."""


@dataclasses.dataclass(frozen=True, slots=True)
class WindowSizeMsg:
    """The terminal was resized, or its size is known for the first time."""

    Width: int = 80
    Height: int = 24


@dataclasses.dataclass(frozen=True, slots=True)
class KeyMsg:
    """One key press, named the way Bubble Tea names it.

    ``Key`` is the canonical name: a single character for a rune, otherwise
    ``enter``, ``tab``, ``esc``, ``up``, ``down``, ``ctrl+c``, ``alt+o``,
    ``shift+enter``, and so on. Bubble Tea's ``KeyMsg.String()`` is the vocabulary
    the features switch on, so the decoder produces exactly that.
    """

    Key: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class QuitMsg:
    """Stop the program."""


@dataclasses.dataclass(frozen=True, slots=True)
class ClearScreenMsg:
    """Clear the live area. Scrollback stays in the terminal."""


async def _quit() -> QuitMsg:
    return QuitMsg()


async def _clear_screen() -> ClearScreenMsg:
    return ClearScreenMsg()


#: The command that stops the program. Go writes it ``tea.Quit``; here it is a
#: value rather than an async function, so a caller passes it, never calls it.
Quit: Final[Command[QuitMsg]] = _quit

#: The command that clears the screen: Go's ``tea.ClearScreen``.
ClearScreen: Final[Command[ClearScreenMsg]] = _clear_screen


@dataclasses.dataclass(frozen=True, slots=True)
class Listener[M]:
    """A command that waits on a channel until the turn it belongs to ends.

    A new turn replaces the previous listener, so the program cancels the task
    the old one runs in. Go cancels nothing: the old goroutine wakes only when
    its channel closes.
    """

    command: Command[M]

    async def __call__(self) -> M | None:
        return await self.command()


@dataclasses.dataclass(frozen=True, slots=True)
class Scrollback:
    """A command that commits text above the live view: Go's ``tea.Println``."""

    Content: str = ""

    async def __call__(self) -> None:
        console = active_console()
        if console is not None and self.Content.strip():
            console.print(self.Content, markup=False, highlight=False, soft_wrap=False)
        return None


def batch[M](*commands: Command[M] | None) -> tuple[Command[M], ...]:
    """Collect commands, dropping the ones that do nothing, as ``tea.Batch`` does."""
    return tuple(command for command in commands if command is not None)


#: The console the running program renders to, for ``tea.Println`` equivalents.
#: ``None`` before a program runs, which makes a printer a no-op in a unit test.
_active_console: Console | None = None


def active_console() -> Console | None:
    """The console the running program owns, if one is running."""
    return _active_console


def _set_active_console(console: Console | None) -> None:
    global _active_console
    _active_console = console


#: How long a lone ``Esc`` waits for the rest of a sequence before it is one key.
_ESCAPE_TIMEOUT = 0.05

#: CSI and SS3 sequences, with the names Bubble Tea gives them.
_SEQUENCES: dict[str, str] = {
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "\x1b[H": "home",
    "\x1b[F": "end",
    "\x1bOA": "up",
    "\x1bOB": "down",
    "\x1bOC": "right",
    "\x1bOD": "left",
    "\x1bOH": "home",
    "\x1bOF": "end",
    "\x1b[3~": "delete",
    "\x1b[5~": "pgup",
    "\x1b[6~": "pgdown",
    "\x1b[Z": "shift+tab",
    "\x1b[13;2u": "shift+enter",
    "\x1b[27;2;13~": "shift+enter",
    "\x1b[13;3u": "alt+enter",
    "\x1b\r": "alt+enter",
    "\x1b\n": "alt+enter",
}

#: Control bytes, with the names Bubble Tea gives them.
_CONTROLS: dict[str, str] = {
    "\x03": "ctrl+c",
    "\x04": "ctrl+d",
    "\x08": "backspace",
    "\x09": "tab",
    "\x0a": "ctrl+j",
    "\x0c": "ctrl+l",
    "\x0d": "enter",
    "\x0f": "ctrl+o",
    "\x14": "ctrl+t",
    "\x15": "ctrl+u",
    "\x19": "ctrl+y",
    "\x7f": "backspace",
}


class KeyDecoder:
    """Translate raw terminal bytes into the key names the features switch on.

    A lone ``Esc`` is indistinguishable from the start of an Alt or CSI sequence
    until the read times out, so an unfinished escape stays buffered: the input
    loop calls :meth:`flush` after an empty read, which is what turns the prefix
    into ``esc`` or ``alt+<rune>``.
    """

    __slots__ = ("_pending",)

    def __init__(self) -> None:
        self._pending = ""

    def feed(self, data: bytes) -> list[str]:
        """Every complete key in ``data``, keeping an unfinished prefix back."""
        self._pending += data.decode("utf-8", errors="ignore")
        keys: list[str] = []
        while self._pending:
            sequence = self._match_sequence(self._pending)
            if sequence is not None:
                text, name = sequence
                keys.append(name)
                self._pending = self._pending[len(text) :]
                continue
            if self._pending.startswith("\x1b"):
                # An unfinished escape: only the timeout can resolve it.
                break
            keys.append(_CONTROLS.get(self._pending[0], self._pending[0]))
            self._pending = self._pending[1:]
        return keys

    def flush(self) -> list[str]:
        """Resolve a buffered escape prefix once the read has timed out.

        A lone ``\\x1b`` is ``esc``, ``\\x1b`` followed by a rune is ``alt+<rune>``,
        and a control sequence this decoder does not know is consumed and
        dropped: it is a key the application has no binding for, never text.
        """
        keys: list[str] = []
        while self._pending.startswith("\x1b"):
            if len(self._pending) == 1:
                keys.append("esc")
                self._pending = ""
                break
            if self._pending[1] in "[O":
                end = _sequence_end(self._pending)
                self._pending = self._pending[end:] if end else ""
                break
            keys.append("alt+" + self._pending[1])
            self._pending = self._pending[2:]
        return keys

    def _match_sequence(self, pending: str) -> tuple[str, str] | None:
        """The longest known key ``pending`` starts with, if one is complete."""
        best: tuple[str, str] | None = None
        for text, name in _SEQUENCES.items():
            if pending.startswith(text) and (best is None or len(text) > len(best[0])):
                best = (text, name)
        if best is not None:
            return best
        if not pending.startswith("\x1b"):
            return None
        if any(text.startswith(pending) for text in _SEQUENCES):
            # A prefix of a known sequence: wait for the rest of it.
            return None
        if len(pending) >= 2 and pending[1] not in "[O":
            return (pending[:2], "alt+" + pending[1])
        return None


def _sequence_end(pending: str) -> int:
    """The index just past a CSI/SS3 sequence's final byte, or ``0`` if unfinished.

    A CSI sequence ends at the first byte in the range 0x40 to 0x7e after the
    introducer, which is how an unrecognised sequence is consumed whole.
    """
    for index in range(2, len(pending)):
        if 0x40 <= ord(pending[index]) <= 0x7E:
            return index + 1
    return 0


def read_keys(decoder: KeyDecoder, fd: int, timeout: float = _ESCAPE_TIMEOUT) -> list[str]:
    """Read every key available on ``fd`` within ``timeout`` seconds.

    The timeout is what separates a lone ``Esc`` from the start of a sequence, so
    an empty read flushes whatever prefix is still buffered.
    """
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return decoder.flush()
    return decoder.feed(os.read(fd, 4096))


@contextlib.contextmanager
def _raw_mode(fd: int) -> Generator[None]:
    """Put ``fd`` in raw mode for the duration, when it is a terminal at all."""
    if not os.isatty(fd):
        yield
        return
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


@dataclasses.dataclass(slots=True)
class Program[Model, MsgT]:
    """The loop: one message at a time, one render, then the commands it asked for.

    ``Model`` is the model an update returns and ``MsgT`` is what it accepts. The
    queue is an :class:`asyncio.Queue` because a command's task posts back to the
    loop while the loop is waiting for the next message.
    """

    model: Model
    update: Callable[[Model, MsgT], Awaitable[tuple[Model, Sequence[Command[MsgT]]]]]
    view: Callable[[Model], RenderableType]
    console: Console
    input_fd: int = 0
    queue: asyncio.Queue[MsgT] = dataclasses.field(default_factory=lambda: asyncio.Queue[Any]())
    tasks: set[asyncio.Task[None]] = dataclasses.field(default_factory=set[asyncio.Task[None]])
    listeners: set[asyncio.Task[None]] = dataclasses.field(default_factory=set[asyncio.Task[None]])
    listener: asyncio.Task[None] | None = None
    live: Live | None = None
    quit: bool = False

    def send(self, message: MsgT) -> None:
        """Post a message into the loop, as ``tea.Program.Send`` does."""
        self.queue.put_nowait(message)

    async def step(self) -> bool:
        """Process one message. Returns ``False`` once the program has quit."""
        return await self.dispatch(await self.queue.get())

    async def dispatch(self, message: MsgT) -> bool:
        """Apply one message: update, render, then start the commands it returned."""
        if isinstance(message, QuitMsg):
            self.quit = True
            return False
        if isinstance(message, ClearScreenMsg):
            self.console.clear()
        self.model, commands = await self.update(self.model, message)
        self.render()
        self._start(commands)
        return True

    def render(self) -> None:
        """Draw the current model, into the live area when one is running."""
        renderable = self.view(self.model)
        if self.live is not None:
            self.live.update(renderable, refresh=True)
            return
        self.console.print(renderable, end="")

    async def run(self) -> None:
        """Render until a ``QuitMsg`` arrives, decoding keys from the terminal."""
        loop = asyncio.get_running_loop()
        width, height = _terminal_size()
        # The program injects its own messages, which every message set carries by
        # construction; a generic parameter cannot say so, hence the cast.
        self.send(cast("MsgT", WindowSizeMsg(Width=width, Height=height)))
        reader = asyncio.create_task(self._read_keys())
        with _raw_mode(self.input_fd), Live(console=self.console, screen=False, auto_refresh=True) as live:
            self.live = live
            _set_active_console(self.console)
            with _sigwinch(loop, self):
                try:
                    while not self.quit:
                        await self.step()
                finally:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                    await self.Shutdown()
        self.live = None
        _set_active_console(None)

    async def _read_keys(self) -> None:
        """Turn terminal input into key messages until the program stops."""
        decoder = KeyDecoder()
        while True:
            for key in await asyncio.to_thread(read_keys, decoder, self.input_fd):
                self.send(cast("MsgT", KeyMsg(Key=key)))

    def _start(self, commands: Sequence[Command[MsgT]]) -> None:
        """Run each command as a task, replacing the previous listener."""
        for command in commands:
            if isinstance(command, Listener):
                # A generic class narrows to its unknown parameter, so the type
                # the caller already fixed is restored here.
                self._cancel_listener()
                task = asyncio.create_task(_drain_command(cast("Listener[MsgT]", command), self.queue))
                self.listener = task
                self.listeners.add(task)
                task.add_done_callback(self.listeners.discard)
                continue
            task = asyncio.create_task(_drain_command(command, self.queue))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    def _cancel_listener(self) -> None:
        """Cancel the listener of the turn that just ended."""
        if self.listener is not None and not self.listener.done():
            self.listener.cancel()
        self.listener = None

    async def Shutdown(self) -> None:
        """Cancel everything still running. The loop has no more messages to send."""
        pending = [*self.tasks, *self.listeners]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _drain_command[M](command: Command[M], queue: asyncio.Queue[Any]) -> None:
    """Await one command and post its message back, ignoring cancellation."""
    with contextlib.suppress(asyncio.CancelledError):
        message = await command()
        if message is not None:
            queue.put_nowait(message)


def _terminal_size() -> tuple[int, int]:
    """The terminal's size, or a sensible default when there is none."""
    try:
        size = os.get_terminal_size(sys.stdout.fileno())
    except (OSError, ValueError):
        return (80, 24)
    return (size.columns, size.lines)


@contextlib.contextmanager
def _sigwinch(loop: asyncio.AbstractEventLoop, program: Program[Any, Any]) -> Generator[None]:
    """Push a :class:`WindowSizeMsg` whenever the terminal is resized."""
    if not hasattr(signal, "SIGWINCH"):
        yield
        return

    def resize() -> None:
        width, height = _terminal_size()
        program.send(WindowSizeMsg(Width=width, Height=height))

    try:
        loop.add_signal_handler(signal.SIGWINCH, resize)
    except (NotImplementedError, RuntimeError, ValueError, OSError):
        # No signal support here — a platform limitation, not a failure.
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(NotImplementedError, RuntimeError, ValueError, OSError):
            loop.remove_signal_handler(signal.SIGWINCH)
