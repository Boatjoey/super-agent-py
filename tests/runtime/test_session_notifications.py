"""What a turn tells the outside world, and in what order.

These cases get their own module because the notification contract is a subject of
its own: a consumer that renders as it reads depends on the order, and a consumer
that appends to a transcript depends on each message being published exactly once.
The session is what publishes notifications while the engine loop runs, so they
exercise the two together.

The fakes are local to this module so it fails on its own terms.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from super_agent.runtime import machine
from super_agent.runtime.engine import NewEngine
from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import ModelResponse, StreamChunk, ToolCall, ToolSpec
from super_agent.runtime.session import (
    NOTIFICATIONS_CLOSED,
    ApprovalDecision,
    ApprovalsClosed,
    MessageAppended,
    NewSession,
    SessionNotification,
    StateChanged,
    StreamChunkReceived,
)


class ScriptedModel:
    """Returns prepared responses in order, optionally streaming first."""

    def __init__(self, responses: list[ModelResponse], stream: str = "") -> None:
        self.responses = list(responses)
        self.stream = stream
        self.calls = 0

    async def Next(
        self,
        ctx: object,
        messages: list[machine.Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        self.calls += 1
        for piece in self.stream:
            on_stream_chunk(StreamChunk(ContentDelta=piece))
        return self.responses.pop(0)


class FakeToolRunner:
    """A tool runner whose only tool is risky, so every call needs approval."""

    def __init__(self, results: dict[str, str] | None = None) -> None:
        self.results = results or {}
        self.ran: list[str] = []

    def Specs(self) -> list[ToolSpec]:
        return [ToolSpec(Name="bash", Description="run a command", Risky=True)]

    async def Run(self, ctx: object, call: ToolCall) -> str:
        self.ran.append(call.Name)
        return self.results.get(call.Name, "ok")


class NoToolRunner:
    """Advertises no tools at all, so a tool call cannot be resolved."""

    def Specs(self) -> list[ToolSpec]:
        return []

    async def Run(self, ctx: object, call: ToolCall) -> str:
        raise AssertionError("no tool may run when none is advertised")


class RecordingCloser:
    def __init__(self, order: list[str], name: str) -> None:
        self.order = order
        self.name = name

    def Close(self) -> None:
        self.order.append(self.name)


async def drain(queue: asyncio.Queue[SessionNotification]) -> list[SessionNotification]:
    """Everything the turn published, up to and including the close marker."""
    seen: list[SessionNotification] = []
    while True:
        item = queue.get_nowait()
        seen.append(item)
        if item is NOTIFICATIONS_CLOSED:
            return seen


def notifications() -> asyncio.Queue[SessionNotification]:
    return asyncio.Queue()


def approvals(*decisions: ApprovalDecision) -> asyncio.Queue[ApprovalDecision | ApprovalsClosed]:
    queue: asyncio.Queue[ApprovalDecision | ApprovalsClosed] = asyncio.Queue()
    for decision in decisions:
        queue.put_nowait(decision)
    return queue


async def ready_engine(model: ScriptedModel, tools: FakeToolRunner | None = None) -> object:
    engine = NewEngine(model, tools or FakeToolRunner(), None)
    await engine.Ready()
    return engine


@pytest.mark.asyncio
async def test_session_run_emits_state_and_final_message() -> None:
    model = ScriptedModel([ModelResponse(Content="hello", ReasoningContent="thinking")])
    engine = await ready_engine(model)
    session = NewSession(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.RunTurn(LiveContext(), "hi", queue, approvals())

    states: list[str] = []
    final: machine.Message | None = None
    for item in await drain(queue):
        if isinstance(item, StateChanged):
            states.append(str(item.State))
        elif isinstance(item, MessageAppended) and item.Message.Role == machine.RoleAssistant:
            final = item.Message

    assert len(states) >= 2
    assert states[0] == machine.StateWaitingLLM
    assert states[-1] == machine.StateIdle
    assert final is not None
    assert final.Content == "hello"
    assert final.ReasoningContent == "thinking"


@pytest.mark.asyncio
async def test_session_run_emits_each_appended_message_once() -> None:
    """The emitter's whole purpose: a repeated snapshot must not repeat a message."""
    model = ScriptedModel([ModelResponse(Content="hello")])
    engine = await ready_engine(model)
    session = NewSession(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.RunTurn(LiveContext(), "hi", queue, approvals())

    appended = [item.Message for item in await drain(queue) if isinstance(item, MessageAppended)]
    assert len(appended) == 2, "user and assistant, each exactly once"
    assert appended[0].Role == machine.RoleUser
    assert appended[0].Content == "hi"
    assert appended[1].Role == machine.RoleAssistant
    assert appended[1].Content == "hello"


@pytest.mark.asyncio
async def test_session_emits_tool_approval_cleared_after_approval() -> None:
    """A cleared approval must be announced, or the prompt stays on screen."""
    model = ScriptedModel(
        [
            ModelResponse(ToolCalls=(ToolCall(ID="call-1", Name="bash", Input='{"command":"pwd"}'),)),
            ModelResponse(Content="done"),
        ]
    )
    tools = FakeToolRunner({"bash": "ok"})
    engine = await ready_engine(model, tools)
    session = NewSession(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.RunTurn(LiveContext(), "run it", queue, approvals(machine.ApproveOnce))

    kinds = [type(item).__name__ for item in await drain(queue)]
    assert "ToolApprovalRequested" in kinds
    assert "ToolApprovalCleared" in kinds
    assert kinds.index("ToolApprovalRequested") < kinds.index("ToolApprovalCleared")
    assert tools.ran == ["bash"]


@pytest.mark.asyncio
async def test_session_stream_event_carries_accumulated_streaming_message() -> None:
    """Each chunk carries the whole accumulated text, not just its own delta."""
    model = ScriptedModel([ModelResponse(Content="abc")], stream="abc")
    engine = await ready_engine(model)
    session = NewSession(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.RunTurn(LiveContext(), "hi", queue, approvals())

    chunks = [item for item in await drain(queue) if isinstance(item, StreamChunkReceived)]
    assert [chunk.Chunk.ContentDelta for chunk in chunks] == ["a", "b", "c"]
    accumulated = [chunk.Message.Content for chunk in chunks if chunk.Message is not None]
    assert accumulated == ["a", "ab", "abc"]


@pytest.mark.asyncio
async def test_session_close_closes_owned_adapters_once() -> None:
    """Close releases every registered closer, newest first, and is idempotent."""
    model = ScriptedModel([])
    engine = await ready_engine(model)
    session = NewSession(engine)  # type: ignore[arg-type]
    order: list[str] = []
    session.AddCloser(RecordingCloser(order, "first"))
    session.AddCloser(RecordingCloser(order, "second"))

    await session.Close()
    await session.Close()

    assert order == ["second", "first"]


@pytest.mark.asyncio
async def test_session_close_reports_every_failing_closer() -> None:
    """One failing sink must not hide another: the errors are aggregated."""

    class Failing:
        def __init__(self, message: str) -> None:
            self.message = message

        def Close(self) -> None:
            raise RuntimeError(self.message)

    model = ScriptedModel([])
    engine = await ready_engine(model)
    session = NewSession(engine)  # type: ignore[arg-type]
    session.AddCloser(Failing("sink a"))
    session.AddCloser(Failing("sink b"))

    with pytest.raises(Exception) as raised:
        await session.Close()

    assert "sink a" in str(raised.value)
    assert "sink b" in str(raised.value)


@pytest.mark.asyncio
async def test_resolver_error_leaves_no_tool_message_when_nothing_was_asked() -> None:
    """A rejected tool batch must not invent a tool result nobody asked for.

    The resolver rejects the call before it reaches the transcript, so the
    assistant never asked for anything and nothing may be answered. Appending a
    tool message with a placeholder call id produces a transcript the provider
    rejects on the next request — and because it is persisted, that failure would
    survive a resume.
    """
    model = ScriptedModel([ModelResponse(ToolCalls=(ToolCall(ID="call-1", Name="bash", Input="pwd"),))])
    engine = NewEngine(model, NoToolRunner(), None)
    await engine.Ready()
    session = NewSession(engine)  # type: ignore[arg-type]
    queue = notifications()

    with pytest.raises(ValueError, match="model returned tool call while tools are disabled"):
        await session.RunTurn(LiveContext(), "use tool", queue, approvals())

    messages = session.Snapshot().Messages
    assert len(messages) == 1
    assert messages[0].Role == machine.RoleUser


@pytest.mark.asyncio
async def test_transcript_survives_a_reset_without_re_emitting_old_messages() -> None:
    """Reset re-arms the emitter, so the next turn republishes only its own work.

    A consumer that appended whatever it was told would otherwise duplicate the
    pre-reset transcript after a reset plus a new turn.
    """
    model = ScriptedModel([ModelResponse(Content="hello"), ModelResponse(Content="hello")])
    engine = await ready_engine(model)
    session = NewSession(engine)  # type: ignore[arg-type]
    first = notifications()
    await session.RunTurn(LiveContext(), "hi", first, approvals())
    await drain(first)

    await session.Reset()

    second = notifications()
    await session.RunTurn(LiveContext(), "again", second, approvals())
    appended = [item.Message.Content for item in await drain(second) if isinstance(item, MessageAppended)]

    assert appended == ["again", "hello"]
