"""Provider registry."""

from __future__ import annotations

from collections.abc import Callable

from super_agent import llm
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Message, ModelResponse, StreamChunk, ToolSpec


class FakeModel:
    """A model that answers without calling out."""

    async def next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        return ModelResponse(content="ok")


def _fake_factory(cfg: llm.ProviderConfig) -> FakeModel:
    return FakeModel()


def test_model_registry_creates_registered_provider() -> None:
    registry = llm.new_model_registry()
    registry.register_configured("fake", _fake_factory)

    model = registry.create("fake", llm.ProviderConfig())
    assert isinstance(model, FakeModel)
