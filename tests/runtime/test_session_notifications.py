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
from super_agent.runtime.engine import new_engine
from super_agent.runtime.protocol.run_context import live_context
from super_agent.runtime.protocol.types import ModelResponse, StreamChunk, ToolCall, ToolSpec, Usage
from super_agent.runtime.session import (
    NOTIFICATIONS_CLOSED,
    ApprovalDecision,
    ApprovalsClosed,
    MessageAppended,
    SessionNotification,
    StateChanged,
    StreamChunkReceived,
    UsageReported,
    new_session,
)


class ScriptedModel:
    """Returns prepared responses in order, optionally streaming first."""

    def __init__(self, responses: list[ModelResponse], stream: str = "") -> None:
        self.responses = list(responses)
        self.stream = stream
        self.calls = 0

    async def next(
        self,
        ctx: object,
        messages: list[machine.Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        self.calls += 1
        for piece in self.stream:
            on_stream_chunk(StreamChunk(content_delta=piece))
        return self.responses.pop(0)


class FakeToolRunner:
    """A tool runner whose only tool is risky, so every call needs approval."""

    def __init__(self, results: dict[str, str] | None = None) -> None:
        self.results = results or {}
        self.ran: list[str] = []

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(name="bash", description="run a command", risky=True)]

    async def run(self, ctx: object, call: ToolCall) -> str:
        self.ran.append(call.name)
        return self.results.get(call.name, "ok")


class NoToolRunner:
    """Advertises no tools at all, so a tool call cannot be resolved."""

    def specs(self) -> list[ToolSpec]:
        return []

    async def run(self, ctx: object, call: ToolCall) -> str:
        raise AssertionError("no tool may run when none is advertised")


class RecordingCloser:
    def __init__(self, order: list[str], name: str) -> None:
        self.order = order
        self.name = name

    def close(self) -> None:
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
    engine = new_engine(model, tools or FakeToolRunner(), None)
    await engine.ready()
    return engine


@pytest.mark.asyncio
async def test_session_run_emits_state_and_final_message() -> None:
    model = ScriptedModel([ModelResponse(content="hello", reasoning_content="thinking")])
    engine = await ready_engine(model)
    session = new_session(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.run_turn(live_context(), "hi", queue, approvals())

    states: list[str] = []
    final: machine.Message | None = None
    for item in await drain(queue):
        if isinstance(item, StateChanged):
            states.append(str(item.state))
        elif isinstance(item, MessageAppended) and item.message.role == machine.ROLE_ASSISTANT:
            final = item.message

    assert len(states) >= 2
    assert states[0] == machine.STATE_WAITING_LLM
    assert states[-1] == machine.STATE_IDLE
    assert final is not None
    assert final.content == "hello"
    assert final.reasoning_content == "thinking"


@pytest.mark.asyncio
async def test_session_run_emits_each_appended_message_once() -> None:
    """The emitter's whole purpose: a repeated snapshot must not repeat a message."""
    model = ScriptedModel([ModelResponse(content="hello")])
    engine = await ready_engine(model)
    session = new_session(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.run_turn(live_context(), "hi", queue, approvals())

    appended = [item.message for item in await drain(queue) if isinstance(item, MessageAppended)]
    assert len(appended) == 2, "user and assistant, each exactly once"
    assert appended[0].role == machine.ROLE_USER
    assert appended[0].content == "hi"
    assert appended[1].role == machine.ROLE_ASSISTANT
    assert appended[1].content == "hello"


@pytest.mark.asyncio
async def test_session_emits_tool_approval_cleared_after_approval() -> None:
    """A cleared approval must be announced, or the prompt stays on screen."""
    model = ScriptedModel(
        [
            ModelResponse(tool_calls=(ToolCall(id="call-1", name="bash", input='{"command":"pwd"}'),)),
            ModelResponse(content="done"),
        ]
    )
    tools = FakeToolRunner({"bash": "ok"})
    engine = await ready_engine(model, tools)
    session = new_session(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.run_turn(live_context(), "run it", queue, approvals(machine.APPROVE_ONCE))

    kinds = [type(item).__name__ for item in await drain(queue)]
    assert "ToolApprovalRequested" in kinds
    assert "ToolApprovalCleared" in kinds
    assert kinds.index("ToolApprovalRequested") < kinds.index("ToolApprovalCleared")
    assert tools.ran == ["bash"]


@pytest.mark.asyncio
async def test_turn_reports_provider_usage() -> None:
    """The provider's own counts reach the interface, before the message they paid for.

    A consumer that renders as it reads shows the cost against the call that
    incurred it, so the report must arrive before the assistant message the
    call produced is announced.
    """
    model = ScriptedModel(
        [ModelResponse(content="hello", usage=Usage(input_tokens=11, output_tokens=7, total_tokens=18))]
    )
    engine = await ready_engine(model)
    session = new_session(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.run_turn(live_context(), "hi", queue, approvals())

    seen = await drain(queue)
    reported = [item.usage for item in seen if isinstance(item, UsageReported)]
    assert len(reported) == 1
    assert reported[0].input_tokens == 11
    assert reported[0].output_tokens == 7
    assert reported[0].total_tokens == 18
    usage_index = next(index for index, item in enumerate(seen) if isinstance(item, UsageReported))
    assistant_index = next(
        index
        for index, item in enumerate(seen)
        if isinstance(item, MessageAppended) and item.message.role == machine.ROLE_ASSISTANT
    )
    assert usage_index < assistant_index


@pytest.mark.asyncio
async def test_turn_reports_usage_for_each_model_call() -> None:
    """A tool round trip makes two model calls, and each reports its own counts.

    The reports arrive in call order, so a consumer that keeps the newest one
    describes the context as it stands now rather than as it started.
    """
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(ToolCall(id="call-1", name="bash", input='{"command":"pwd"}'),),
                usage=Usage(input_tokens=5, output_tokens=2, total_tokens=7),
            ),
            ModelResponse(content="done", usage=Usage(input_tokens=20, output_tokens=3, total_tokens=23)),
        ]
    )
    engine = await ready_engine(model, FakeToolRunner({"bash": "ok"}))
    session = new_session(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.run_turn(live_context(), "run it", queue, approvals(machine.APPROVE_ONCE))

    reported = [item.usage for item in await drain(queue) if isinstance(item, UsageReported)]
    assert [usage.total_tokens for usage in reported] == [7, 23]


@pytest.mark.asyncio
async def test_turn_reports_no_usage_when_the_provider_measured_nothing() -> None:
    """An unmeasured response reports nothing rather than zeroes.

    A provider that cannot report usage must leave the interface without a
    figure to show, not with a false one.
    """
    model = ScriptedModel([ModelResponse(content="hello")])
    engine = await ready_engine(model)
    session = new_session(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.run_turn(live_context(), "hi", queue, approvals())

    reported = [item for item in await drain(queue) if isinstance(item, UsageReported)]
    assert reported == []


@pytest.mark.asyncio
async def test_session_stream_event_carries_accumulated_streaming_message() -> None:
    """Each chunk carries the whole accumulated text, not just its own delta."""
    model = ScriptedModel([ModelResponse(content="abc")], stream="abc")
    engine = await ready_engine(model)
    session = new_session(engine)  # type: ignore[arg-type]
    queue = notifications()

    await session.run_turn(live_context(), "hi", queue, approvals())

    chunks = [item for item in await drain(queue) if isinstance(item, StreamChunkReceived)]
    assert [chunk.chunk.content_delta for chunk in chunks] == ["a", "b", "c"]
    accumulated = [chunk.message.content for chunk in chunks if chunk.message is not None]
    assert accumulated == ["a", "ab", "abc"]


@pytest.mark.asyncio
async def test_session_close_closes_owned_adapters_once() -> None:
    """Close releases every registered closer, newest first, and is idempotent."""
    model = ScriptedModel([])
    engine = await ready_engine(model)
    session = new_session(engine)  # type: ignore[arg-type]
    order: list[str] = []
    session.add_closer(RecordingCloser(order, "first"))
    session.add_closer(RecordingCloser(order, "second"))

    await session.close()
    await session.close()

    assert order == ["second", "first"]


@pytest.mark.asyncio
async def test_session_close_reports_every_failing_closer() -> None:
    """One failing sink must not hide another: the errors are aggregated."""

    class Failing:
        def __init__(self, message: str) -> None:
            self.message = message

        def close(self) -> None:
            raise RuntimeError(self.message)

    model = ScriptedModel([])
    engine = await ready_engine(model)
    session = new_session(engine)  # type: ignore[arg-type]
    session.add_closer(Failing("sink a"))
    session.add_closer(Failing("sink b"))

    with pytest.raises(Exception) as raised:
        await session.close()

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
    model = ScriptedModel([ModelResponse(tool_calls=(ToolCall(id="call-1", name="bash", input="pwd"),))])
    engine = new_engine(model, NoToolRunner(), None)
    await engine.ready()
    session = new_session(engine)  # type: ignore[arg-type]
    queue = notifications()

    with pytest.raises(ValueError, match="model returned tool call while tools are disabled"):
        await session.run_turn(live_context(), "use tool", queue, approvals())

    messages = session.snapshot().messages
    assert len(messages) == 1
    assert messages[0].role == machine.ROLE_USER


@pytest.mark.asyncio
async def test_transcript_survives_a_reset_without_re_emitting_old_messages() -> None:
    """Reset re-arms the emitter, so the next turn republishes only its own work.

    A consumer that appended whatever it was told would otherwise duplicate the
    pre-reset transcript after a reset plus a new turn.
    """
    model = ScriptedModel([ModelResponse(content="hello"), ModelResponse(content="hello")])
    engine = await ready_engine(model)
    session = new_session(engine)  # type: ignore[arg-type]
    first = notifications()
    await session.run_turn(live_context(), "hi", first, approvals())
    await drain(first)

    await session.reset()

    second = notifications()
    await session.run_turn(live_context(), "again", second, approvals())
    appended = [item.message.content for item in await drain(second) if isinstance(item, MessageAppended)]

    assert appended == ["again", "hello"]
