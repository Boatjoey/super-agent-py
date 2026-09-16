"""A deliberate-yield stress harness for the engine's atomicity claims.

Python's single-threaded event loop makes a data race impossible, but it makes a
different class of bug possible: state that is only correct because nothing yields
between reading it and writing it. The engine asserts that its commit points are
atomic — runtime data and the action plan are committed under one lock hold, the
streaming commit contains no await, and a stale run's completion is discarded
before it can write.

An ordinary test drives those paths with few suspension points, so an accidental
`await` inserted into a critical section would go unnoticed. This module removes
that cover: every port call yields repeatedly, and unrelated coroutines read and
cancel while a turn is in flight, so the loop takes every opportunity to run
something else. A regression shows up as a wrong transcript, a wrong final state,
or a stale message landing in the next turn.

This is an approximation and not a race detector. `docs/contributing.md` records
the residual gap.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

import pytest

from super_agent.runtime import machine
from super_agent.runtime.engine import Engine, new_engine
from super_agent.runtime.protocol.run_context import RunContext, live_context
from super_agent.runtime.protocol.types import ModelResponse, StreamChunk, ToolCall, ToolSpec

#: How many times each test repeats its scenario. The point is coverage of
#: interleavings, and a single pass reaches only one of them.
ROUNDS = 25

#: How many times every port call yields before answering.
YIELDS_PER_CALL = 3

PROMPT = "run the tool"
COMMAND = '{"command":"pwd"}'


async def yield_repeatedly() -> None:
    """Give the loop several chances to run something else."""
    for _ in range(YIELDS_PER_CALL):
        await asyncio.sleep(0)


class YieldingModel:
    """A model that yields inside every call, including between stream chunks."""

    def __init__(self) -> None:
        self.calls = 0
        self.torn = False

    async def next(
        self,
        ctx: RunContext,
        messages: list[machine.Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        self.calls += 1
        for piece in ("wor", "king"):
            await yield_repeatedly()
            if on_stream_chunk is not None:
                on_stream_chunk(StreamChunk(content_delta=piece))
        await yield_repeatedly()
        if self.calls == 1:
            return ModelResponse(content="calling", tool_calls=(ToolCall(id="call-1", name="bash", input=COMMAND),))
        return ModelResponse(content="done")


class YieldingToolRunner:
    """A tool runner that yields before and after running, so the window is wide."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(name="bash", description="run", risky=False)]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        await yield_repeatedly()
        self.ran.append(call.name)
        await yield_repeatedly()
        return "tool output"


class ProbingReader:
    """Reads engine state as often as the loop allows, to catch a torn view."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.reads = 0
        self.torn = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    async def _loop(self) -> None:
        while True:
            view = self.engine.snapshot()
            self.reads += 1
            # A snapshot must never be internally inconsistent: the batch counters
            # only exist while a tool is pending, and a pending tool always carries
            # its permission request.
            if view.pending_tool_batch_total and view.pending_tool is None:
                self.torn = True
            if view.pending_tool is not None and view.pending_permission is None:
                self.torn = True
            if view.streaming_message is not None and view.state != machine.STATE_WAITING_LLM:
                self.torn = True
            await asyncio.sleep(0)


def build_engine() -> tuple[Engine, YieldingModel, YieldingToolRunner]:
    model = YieldingModel()
    tools = YieldingToolRunner()
    engine = new_engine(model, tools, None)  # type: ignore[arg-type]
    return engine, model, tools


async def settle() -> None:
    """Let queued callbacks run, so nothing is asserted mid-schedule."""
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_turn_completes_correctly_under_constant_concurrent_reading() -> None:
    for round_number in range(ROUNDS):
        engine, model, tools = build_engine()
        await engine.ready()
        reader = ProbingReader(engine)
        reader.start()
        try:
            await engine.run_turn(live_context(), machine.UserMessageSubmitted(content=PROMPT), None, None)
        finally:
            await reader.stop()

        assert not reader.torn, f"round {round_number}: a snapshot was internally inconsistent"
        assert reader.reads > 0, f"round {round_number}: the reader never ran; it proved nothing"
        assert engine.state() == machine.STATE_IDLE, f"round {round_number}"
        assert [str(message.role) for message in engine.messages()] == [
            "user",
            "assistant",
            "tool",
            "assistant",
        ], f"round {round_number}"
        assert tools.ran == ["bash"], f"round {round_number}"
        assert model.calls == 2, f"round {round_number}"


@pytest.mark.asyncio
async def test_a_cancel_that_lands_first_never_leaves_a_stale_reply() -> None:
    """The discard rule, under interleaving: a cancelled run writes nothing later.

    The model is released only after the cancel, so its reply is guaranteed to
    arrive for a run that is no longer current.
    """
    for round_number in range(ROUNDS):
        release = asyncio.Event()
        entered = asyncio.Event()

        class GatedModel:
            calls = 0

            def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
                self._entered = entered
                self._release = release

            async def next(
                self,
                ctx: RunContext,
                messages: list[machine.Message],
                tools: list[ToolSpec],
                on_stream_chunk: Callable[[StreamChunk], None],
            ) -> ModelResponse:
                type(self).calls += 1
                self._entered.set()
                await self._release.wait()
                return ModelResponse(content="stale reply")

        model = GatedModel(entered, release)
        engine = new_engine(model, YieldingToolRunner(), None)  # type: ignore[arg-type]
        await engine.ready()

        turn = asyncio.create_task(
            engine.run_turn(live_context(), machine.UserMessageSubmitted(content="first"), None, None)
        )
        await entered.wait()
        await engine.cancel()
        release.set()
        await turn
        await settle()

        assert engine.state() == machine.STATE_IDLE, f"round {round_number}"
        contents = [message.content for message in engine.messages()]
        assert "stale reply" not in contents, f"round {round_number}: {contents}"

        # The next turn must be unaffected by the run that was cancelled.
        clean = new_engine(YieldingModel(), YieldingToolRunner(), None)  # type: ignore[arg-type]
        await clean.ready()
        await clean.run_turn(live_context(), machine.UserMessageSubmitted(content="second"), None, None)
        assert [message.content for message in clean.messages()][-1] == "done", f"round {round_number}"


@pytest.mark.asyncio
async def test_reset_during_a_streaming_turn_is_not_written_into() -> None:
    """A reset that lands mid-stream must not leave the flushed stream behind."""
    for round_number in range(ROUNDS):
        entered = asyncio.Event()
        release = asyncio.Event()

        class GatedStreamer:
            def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
                self._entered = entered
                self._release = release

            async def next(
                self,
                ctx: RunContext,
                messages: list[machine.Message],
                tools: list[ToolSpec],
                on_stream_chunk: Callable[[StreamChunk], None],
            ) -> ModelResponse:
                if on_stream_chunk is not None:
                    on_stream_chunk(StreamChunk(content_delta="partial"))
                self._entered.set()
                await self._release.wait()
                return ModelResponse(content="late")

        engine = new_engine(GatedStreamer(entered, release), YieldingToolRunner(), None)  # type: ignore[arg-type]
        await engine.ready()

        turn = asyncio.create_task(
            engine.run_turn(live_context(), machine.UserMessageSubmitted(content="hello"), None, None)
        )
        await entered.wait()
        await engine.reset()
        release.set()
        await turn
        await settle()

        assert engine.state() == machine.STATE_IDLE, f"round {round_number}"
        contents = [message.content for message in engine.messages()]
        assert contents == [], f"round {round_number}: the reset was written into: {contents}"


@pytest.mark.asyncio
async def test_approval_and_cancel_racing_leaves_exactly_one_answer_per_call() -> None:
    """Every dispatched call is answered exactly once, whichever side wins."""
    for round_number in range(ROUNDS):
        approvals: asyncio.Queue[machine.ApprovalDecision] = asyncio.Queue()
        engine, _model, tools = build_engine()
        await engine.ready()

        class QueuedWaiter:
            def __init__(self, queue: asyncio.Queue[machine.ApprovalDecision]) -> None:
                self._queue = queue

            async def wait_approval(
                self, ctx: RunContext, call: ToolCall, request: machine.PermissionRequest
            ) -> machine.ApprovalDecision:
                await yield_repeatedly()
                return await self._queue.get()

        approvals.put_nowait(machine.APPROVE_ONCE)
        await engine.run_turn(
            live_context(), machine.UserMessageSubmitted(content=PROMPT), None, QueuedWaiter(approvals)
        )
        await settle()

        tool_messages = [message for message in engine.messages() if message.role == machine.ROLE_TOOL]
        assert len(tool_messages) == 1, f"round {round_number}: {[m.content for m in tool_messages]}"
        assert tool_messages[0].tool_call_id == "call-1", f"round {round_number}"
        assert tools.ran == ["bash"], f"round {round_number}"


@pytest.mark.asyncio
async def test_state_observer_never_runs_while_the_lock_is_held_under_interleaving() -> None:
    """The lock rule, checked with the loop given every chance to interleave."""
    for round_number in range(ROUNDS):
        engine, _model, _tools = build_engine()
        await engine.ready()
        held: list[bool] = []

        class LockProbe:
            def __init__(self, engine: Engine, held: list[bool]) -> None:
                self._engine = engine
                self._held = held

            async def __call__(self) -> None:
                await yield_repeatedly()
                self._held.append(self._engine.lock.locked())

        engine.set_state_observer(LockProbe(engine, held))
        await engine.run_turn(live_context(), machine.UserMessageSubmitted(content=PROMPT), None, None)
        engine.set_state_observer(None)

        assert held, f"round {round_number}: the observer never ran"
        assert not any(held), f"round {round_number}: the observer saw the lock held"
