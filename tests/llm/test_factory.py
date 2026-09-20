"""Provider registry."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from super_agent import llm
from super_agent.llm import factory as model_factory
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


@pytest.mark.asyncio
async def test_builtin_provider_is_loaded_on_first_request(monkeypatch: pytest.MonkeyPatch) -> None:
    imported: list[str] = []

    def import_module(name: str) -> SimpleNamespace:
        imported.append(name)
        return SimpleNamespace(new_open_ai=lambda _config: FakeModel())

    monkeypatch.setattr(model_factory.importlib, "import_module", import_module)
    model = llm.new_model("openai", llm.ProviderConfig(api_key="test", model="test"))

    assert imported == []
    response = await model.next(RunContext(), [], [], lambda _chunk: None)
    assert response.content == "ok"
    assert imported == ["super_agent.llm.openai"]

    await model.next(RunContext(), [], [], lambda _chunk: None)
    assert imported == ["super_agent.llm.openai"]
