"""Native, line-oriented terminal shell.

The shell writes committed output to normal terminal scrollback and reads prompts
from stdin. It owns no alternate screen, retained viewport, pane, or overlay.
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

from rich.console import Console

from super_agent.tui import runtime
from super_agent.tui.app import App, Msg
from super_agent.tui.transcript import ROLE_USER
from super_agent.tui.update import update

__all__ = ["TerminalApplication"]

type InputReader = Callable[[str], Awaitable[str]]


async def _read_input(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


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
        self.model.printOutput = self._output_printer

    async def run_terminal(self) -> None:
        """Run without switching screens or taking over terminal scrolling."""
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

    async def _read_prompt(self) -> None:
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
        messages = self.model.transcript.messages
        for message in messages[self._rendered_messages :]:
            if message.role != ROLE_USER:
                self._console.print(self.model.transcript.renderCommitted(message, False, False))
        self._rendered_messages = len(messages)

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
