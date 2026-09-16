"""Provider registry, ported from ``tests/llm/factory_test.go``."""

from __future__ import annotations

from collections.abc import Callable

from super_agent import llm
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Message, ModelResponse, StreamChunk, ToolSpec


class FakeModel:
    """The Go test's ``fakeModel``: a model that answers without calling out."""

    async def Next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        return ModelResponse(Content="ok")


def _fake_factory(cfg: llm.ProviderConfig) -> FakeModel:
    return FakeModel()


def test_model_registry_creates_registered_provider() -> None:
    registry = llm.NewModelRegistry()
    registry.RegisterConfigured("fake", _fake_factory)

    model = registry.Create("fake", llm.ProviderConfig())
    assert isinstance(model, FakeModel)
