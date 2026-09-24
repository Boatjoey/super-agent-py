"""Native, line-oriented terminal shell.

The shell writes committed output to normal terminal scrollback and reads prompts
from stdin. It owns no alternate screen, retained viewport, pane, or overlay.
"""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import select
import shutil
import signal
import sys
import termios
import tty
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

from rich.console import Console
from rich.text import Text

from super_agent.tui import runtime
from super_agent.tui.app import App, Msg
from super_agent.tui.transcript import ROLE_ASSISTANT, ROLE_USER
from super_agent.tui.update import update

__all__ = ["TerminalApplication"]

type InputReader = Callable[[str], Awaitable[str]]


async def _read_input(prompt: str) -> str:
    return await asyncio.to_thread(_read_line, prompt)


def _read_line(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = sys.stdin.buffer.readline()
    if not line:
        raise EOFError
    return line.decode("utf-8", errors="replace").rstrip("\r\n")


def _read_key(fd: int) -> str:
    if not select.select([fd], [], [], 0.1)[0]:
        return ""
    first = os.read(fd, 1)
    if not first:
        return "ctrl+d"
    if first == b"\x1b":
        sequence = bytearray(first)
        while len(sequence) < 3 and select.select([fd], [], [], 0.03)[0]:
            sequence.extend(os.read(fd, 1))
        return {
            b"\x1b[A": "up",
            b"\x1b[B": "down",
            b"\x1b[C": "right",
            b"\x1b[D": "left",
        }.get(bytes(sequence), "esc")
    controls = {
        b"\r": "enter",
        b"\n": "enter",
        b"\t": "tab",
        b"\x7f": "backspace",
        b"\x08": "backspace",
        b"\x03": "ctrl+c",
        b"\x04": "ctrl+d",
        b"\x15": "ctrl+u",
    }
    if first in controls:
        return controls[first]
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    character = decoder.decode(first)
    while not character:
        character = decoder.decode(os.read(fd, 1))
    return character


class TerminalApplication:
    """Run the UI as ordinary stdin, stdout, and terminal scrollback."""

    def __init__(
        self,
        model: App,
        *,
        reader: InputReader = _read_input,
        console: Console | None = None,
    ) -> None:
        self.model = model
        self._reader = reader
        self._console = console or Console(highlight=False)
        self._messages: asyncio.Queue[Msg] = asyncio.Queue()
        self._tasks: set[asyncio.Task[None]] = set()
        self._listener: asyncio.Task[None] | None = None
        self._running = True
        self._rendered_messages = 0
        self._last_error = ""
        self._last_status = ""
        self._streamed_content = ""
        self._streamed_reasoning = ""
        self._live_input = reader is _read_input and sys.stdin.isatty() and sys.stdout.isatty()
        self.model.printOutput = self._output_printer

    async def run_terminal(self) -> None:
        """Run without switching screens or taking over terminal scrolling."""
        loop = asyncio.get_running_loop()
        previous_sigint = signal.getsignal(signal.SIGINT)
        loop.add_signal_handler(signal.SIGINT, self._messages.put_nowait, runtime.KeyMsg(key="ctrl+c"))
        width, height = shutil.get_terminal_size((80, 24))
        self._console.print(self.model.transcript.welcome)
        await self._dispatch(runtime.WindowSizeMsg(width=width, height=height))
        self._start(self.model.init())
        try:
            while self._running:
                if self.model.approval.active() and not self.model.approval.submitted:
                    await self._read_approval()
                elif not self._messages.empty():
                    await self._dispatch(self._messages.get_nowait())
                elif self.model.cancellation is None:
                    await asyncio.sleep(0)
                    if not self._messages.empty():
                        continue
                    await self._read_prompt()
                else:
                    await self._dispatch(await self._messages.get())
        finally:
            await self._close()
            loop.remove_signal_handler(signal.SIGINT)
            signal.signal(signal.SIGINT, previous_sigint)

    async def _read_prompt(self) -> None:
        if self._live_input:
            await self._read_live_prompt()
            return
        try:
            text = await self._reader("\n\u276f ")
        except (EOFError, KeyboardInterrupt):
            self._running = False
            return
        if not text.strip():
            return
        self.model.composer.value = text
        self.model.composer.cursor = len(text)
        await self._dispatch(runtime.KeyMsg(key="enter"))

    async def _read_live_prompt(self) -> None:
        fd = sys.stdin.fileno()
        original = termios.tcgetattr(fd)
        lines = 0
        try:
            tty.setcbreak(fd)
            settings = termios.tcgetattr(fd)
            settings[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSADRAIN, settings)
            self._draw_prompt(lines)
            lines = self._prompt_lines()
            while self._running and self.model.cancellation is None:
                if not self._messages.empty():
                    self._clear_prompt(lines)
                    lines = 0
                    await self._dispatch(self._messages.get_nowait())
                    if self._running and self.model.cancellation is None:
                        self._draw_prompt(0)
                        lines = self._prompt_lines()
                    continue
                key = await asyncio.to_thread(_read_key, fd)
                if not key:
                    continue
                if key == "ctrl+d" and not self.model.composer.draft():
                    self._running = False
                    break
                if key == "ctrl+c":
                    self._running = False
                    break
                self._clear_prompt(lines)
                lines = 0
                draft = self.model.composer.draft()
                matches = self.model.composer.matches()
                completing = bool(matches and matches[self.model.composer.selection].name != draft)
                if key == "enter" and not completing:
                    sys.stdout.write("\u276f " + draft + "\n")
                    sys.stdout.flush()
                await self._dispatch(runtime.KeyMsg(key=key))
                if self.model.cancellation is None and self._running:
                    self._draw_prompt(0)
                    lines = self._prompt_lines()
        finally:
            if lines:
                self._clear_prompt(lines)
            termios.tcsetattr(fd, termios.TCSADRAIN, original)
            sys.stdout.write("\n")
            sys.stdout.flush()

    def _prompt_lines(self) -> int:
        return len(self.model.composer.paletteView().plain.splitlines()) + 1 if self.model.composer.matches() else 1

    def _draw_prompt(self, old_lines: int) -> None:
        if old_lines:
            self._clear_prompt(old_lines)
        palette = self.model.composer.paletteView().plain
        if palette:
            sys.stdout.write(palette + "\n")
        sys.stdout.write("\u276f " + self.model.composer.draft())
        sys.stdout.flush()

    @staticmethod
    def _clear_prompt(lines: int) -> None:
        sys.stdout.write("\r\x1b[2K")
        for _ in range(lines - 1):
            sys.stdout.write("\x1b[1A\r\x1b[2K")
        sys.stdout.flush()

    async def _read_approval(self) -> None:
        self._console.print(self.model.approval.view(self.model.info.cwd))
        try:
            answer = (await self._reader("Approve [1] once, [2] always, [3] deny: ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "3"
        await self._dispatch(runtime.KeyMsg(key={"y": "1", "a": "2", "n": "3"}.get(answer, answer)))

    async def _dispatch(self, message: Msg) -> None:
        if isinstance(message, runtime.QuitMsg):
            self._running = False
            return
        if isinstance(message, runtime.ClearScreenMsg):
            self.model.err = ""
            self.model.status = ""
        self.model, commands = await update(self.model, message)
        self._render()
        self._start(commands)

    def _render(self) -> None:
        streaming = self.model.transcript.streaming
        if streaming is not None:
            if streaming.reasoning_content.startswith(self._streamed_reasoning):
                reasoning = streaming.reasoning_content[len(self._streamed_reasoning) :]
                if reasoning:
                    self._console.print(Text(reasoning, style=self.model.styles.secondary), end="")
            if streaming.content.startswith(self._streamed_content):
                content = streaming.content[len(self._streamed_content) :]
                if content:
                    if self._streamed_reasoning and not self._streamed_content:
                        self._console.print()
                    self._console.print(content, end="", markup=False, highlight=False)
            self._streamed_reasoning = streaming.reasoning_content
            self._streamed_content = streaming.content
        messages = self.model.transcript.messages
        for message in messages[self._rendered_messages :]:
            if message.role != ROLE_USER:
                if message.role == ROLE_ASSISTANT and (self._streamed_content or self._streamed_reasoning):
                    if message.content.startswith(self._streamed_content):
                        self._console.print(
                            message.content[len(self._streamed_content) :], markup=False, highlight=False
                        )
                    else:
                        self._console.print()
                        self._console.print(self.model.transcript.renderCommitted(message, False, False))
                    self._streamed_content = ""
                    self._streamed_reasoning = ""
                else:
                    self._console.print(self.model.transcript.renderCommitted(message, False, False))
        self._rendered_messages = len(messages)
        if streaming is None and self._streamed_content and self.model.cancellation is None:
            self._console.print()
            self._streamed_content = ""
            self._streamed_reasoning = ""

        if self.model.err and self.model.err != self._last_error:
            self._console.print(f"error: {self.model.err}", style=self.model.styles.error)
        elif self.model.status and self.model.status != self._last_status:
            self._console.print(self.model.status, style=self.model.styles.accent)
        self._last_error = self.model.err
        self._last_status = self.model.status

        if self.model.showHelp:
            self._console.print(
                "Enter a prompt normally. Slash commands start with /. Use /commands to list them and /quit to exit."
            )
            self.model.showHelp = False

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
                    await self._messages.put(value)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _output_printer(self, content: str) -> runtime.Command[Msg] | None:
        if not content.strip():
            return None

        async def show() -> None:
            self._console.print(content)

        return show

    async def _close(self) -> None:
        pending = list(self._tasks)
        if self._listener is not None and self._listener not in pending:
            pending.append(self._listener)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
