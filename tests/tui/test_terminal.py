"""The production shell uses ordinary terminal scrollback."""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable, Sequence

import pytest
from rich.console import Console

from super_agent.tui import APPROVE_ONCE, ROLE_ASSISTANT, Message, MessageAppended
from super_agent.tui.terminal import TerminalApplication
from tests.tui.test_app import ApprovalConversation, FakeConversation, new_app


def reader(values: Sequence[str]) -> Callable[[str], Awaitable[str]]:
    pending = iter(values)

    async def read(_prompt: str) -> str:
        return next(pending)

    return read


@pytest.mark.asyncio
async def test_terminal_appends_reply_to_normal_output() -> None:
    output = io.StringIO()
    fake = FakeConversation(script=[MessageAppended(message=Message(role=ROLE_ASSISTANT, content="native reply"))])
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
