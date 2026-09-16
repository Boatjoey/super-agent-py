"""Model fakes shared by the engine tests.

Ported alongside ``tests/runtime/engine_test.go``'s ``scriptedModel``,
``streamingCancelModel``, and ``blockingModel``. They live here rather than in a
test module because more than one test file drives a model, and because the
engine tests need to hold a call open while the rest of the loop keeps running.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Message, ModelResponse, StreamChunk, ToolSpec

__all__ = ["BlockingModel", "GateModel", "ScriptedModel", "StreamingCancelModel"]


class ScriptedModel:
    """Replays one response per call and records the last message it was sent.

    Mirrors Go's ``scriptedModel``: ``Next`` appends the final input message to
    ``calls`` and pops the next scripted response, so a test can assert both what
    the model was shown and how many times it was called.
    """

    def __init__(self, responses: Sequence[ModelResponse]) -> None:
        self.responses: list[ModelResponse] = list(responses)
        self.calls: list[Message] = []

    async def Next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        self.calls.append(messages[-1])
        return self.responses.pop(0)


class StreamingCancelModel:
    """Streams one chunk, then blocks until its context is cancelled.

    Mirrors Go's ``streamingCancelModel``. The chunk is delivered *before* the
    block, so a cancel while the call is in flight must flush it as an
    interrupted assistant message.
    """

    def __init__(self) -> None:
        self.started: asyncio.Event = asyncio.Event()

    async def Next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        on_stream_chunk(StreamChunk(ContentDelta="partial", ReasoningContentDelta="thinking"))
        self.started.set()
        await ctx.Done().wait()
        ctx.RaiseIfCancelled()
        return ModelResponse()


class BlockingModel:
    """Blocks every call on ``release``, then returns ``content``.

    Mirrors Go's ``blockingModel``: the point is a call that is still in flight
    when the test cancels or resets the run.
    """

    def __init__(self, release: asyncio.Event, content: str = "stale") -> None:
        self.release: asyncio.Event = release
        self.content: str = content
        self.started: asyncio.Event = asyncio.Event()
        self.calls: int = 0

    async def Next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return ModelResponse(Content=self.content)


class GateModel:
    """Blocks the first call on ``release``; later calls answer immediately.

    The first response is the stale one a cancelled run must not apply; the
    second is what the run that replaced it is allowed to commit.
    """

    def __init__(self, release: asyncio.Event, responses: Sequence[ModelResponse]) -> None:
        self.release: asyncio.Event = release
        self.responses: list[ModelResponse] = list(responses)
        self.started: asyncio.Event = asyncio.Event()
        self.calls: list[Message] = []

    async def Next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        self.calls.append(messages[-1])
        index = len(self.calls) - 1
        if index == 0:
            self.started.set()
            await self.release.wait()
        return self.responses[index]
