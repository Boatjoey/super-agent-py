"""The production shell uses ordinary terminal scrollback."""

from __future__ import annotations

import io
import sys
from collections.abc import Awaitable, Callable, Sequence

import pytest
from rich.console import Console

from super_agent.tui import APPROVE_ONCE, ROLE_ASSISTANT, Message, MessageAppended, StreamChunkReceived
from super_agent.tui.terminal import TerminalApplication, _read_line
from tests.tui.fakes import ApprovalConversation, FakeConversation, new_app


def reader(values: Sequence[str]) -> Callable[[str], Awaitable[str]]:
    pending = iter(values)

    async def read(_prompt: str) -> str:
        return next(pending)

    return read


def test_terminal_input_accepts_chinese_and_replaces_invalid_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    stdin = io.TextIOWrapper(io.BytesIO("请介绍".encode() + b"\n" + "请结".encode() + b"\xe4\n"), encoding="utf-8")
    monkeypatch.setattr(sys, "stdin", stdin)

    assert _read_line("") == "请介绍"
    assert _read_line("") == "请结�"


@pytest.mark.asyncio
async def test_terminal_appends_reply_to_normal_output() -> None:
    output = io.StringIO()
    fake = FakeConversation([MessageAppended(message=Message(role=ROLE_ASSISTANT, content="native reply"))])
    program = TerminalApplication(
        new_app(fake),
        reader=reader(("hello", "/quit")),
        console=Console(file=output, force_terminal=False, width=80),
    )

    await program.run_terminal()

    assert fake.queries == ["hello"]
    assert "native reply" in output.getvalue()
    assert "\x1b[?1049h" not in output.getvalue(), "the shell never enters the alternate screen"


@pytest.mark.asyncio
async def test_terminal_streams_reply_once() -> None:
    output = io.StringIO()
    fake = FakeConversation(
        [
            StreamChunkReceived(message=Message(role=ROLE_ASSISTANT, content="你好")),
            StreamChunkReceived(message=Message(role=ROLE_ASSISTANT, content="你好，世界")),
            MessageAppended(message=Message(role=ROLE_ASSISTANT, content="你好，世界")),
        ]
    )
    program = TerminalApplication(
        new_app(fake),
        reader=reader(("介绍项目", "/quit")),
        console=Console(file=output, force_terminal=False, width=80),
    )

    await program.run_terminal()

    assert output.getvalue().count("你好，世界") == 1
    assert fake.queries == ["介绍项目"]


@pytest.mark.asyncio
async def test_terminal_reads_inline_approval() -> None:
    output = io.StringIO()
    fake = ApprovalConversation()
    program = TerminalApplication(
        new_app(fake),
        reader=reader(("run it", "1", "/quit")),
        console=Console(file=output, force_terminal=False, width=80),
    )

    await program.run_terminal()

    assert fake.decisions == [APPROVE_ONCE]
    assert "ACTION REQUIRED" in output.getvalue()


@pytest.mark.asyncio
async def test_terminal_writes_slash_command_output_to_scrollback() -> None:
    output = io.StringIO()
    program = TerminalApplication(
        new_app(FakeConversation()),
        reader=reader(("/diff", "/quit")),
        console=Console(file=output, force_terminal=False, width=80),
    )

    await program.run_terminal()

    rendered = output.getvalue()
    assert "diff" in rendered
    assert "\x1b[?1049h" not in rendered


@pytest.mark.asyncio
async def test_terminal_help_is_inline() -> None:
    output = io.StringIO()
    program = TerminalApplication(
        new_app(FakeConversation()),
        reader=reader(("/help", "/quit")),
        console=Console(file=output, force_terminal=False, width=80),
    )

    await program.run_terminal()

    assert "Slash commands start with /" in output.getvalue()
