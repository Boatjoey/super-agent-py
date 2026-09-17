"""Provider registry and factory.

A provider name maps to a factory that builds a
:class:`~super_agent.runtime.protocol.types.Model`. The registry of built-in
providers contains deferred adapters, so importing this package and constructing
a model do not import an SDK before the first request.
"""

from __future__ import annotations

import dataclasses
import functools
import importlib
from collections.abc import Callable
from typing import Final, cast

from super_agent.jsonutil import json_field
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Message, Model, ModelResponse, StreamChunk, ToolSpec

#: The provider used when a caller names none.
DEFAULT_PROVIDER: Final[str] = "deepseek"


@dataclasses.dataclass(frozen=True, slots=True)
class ProviderConfig:
    """How one provider is configured.

    Field names and JSON keys are fixed by the settings file.
    """

    base_url: str = dataclasses.field(default="", metadata=json_field(name="base_url"))
    api_key: str = dataclasses.field(default="", metadata=json_field(name="api_key"))
    model: str = dataclasses.field(default="", metadata=json_field(name="model"))


#: Builds the model for one provider.
ModelFactory = Callable[[ProviderConfig], Model]


class _DeferredModel:
    """Load one provider adapter when its first request starts."""

    __slots__ = ("_config", "_factory_name", "_model", "_module_name")

    def __init__(self, module_name: str, factory_name: str, config: ProviderConfig) -> None:
        self._module_name = module_name
        self._factory_name = factory_name
        self._config = config
        self._model: Model | None = None

    async def next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        model = self._model
        if model is None:
            module = importlib.import_module(self._module_name)
            factory = cast(ModelFactory, getattr(module, self._factory_name))
            model = factory(self._config)
            self._model = model
        return await model.next(ctx, messages, tools, on_stream_chunk)


def _deferred_deepseek(config: ProviderConfig) -> Model:
    return _DeferredModel("super_agent.llm.deepseek", "new_deep_seek", config)


def _deferred_openai(config: ProviderConfig) -> Model:
    return _DeferredModel("super_agent.llm.openai", "new_open_ai", config)


def _deferred_claude(config: ProviderConfig) -> Model:
    return _DeferredModel("super_agent.llm.claude", "new_claude", config)


class ModelRegistry:
    """A provider-name to model-factory table."""

    def __init__(self) -> None:
        self.factories: dict[str, ModelFactory] = {}

    def register_configured(self, provider: str, factory: ModelFactory) -> None:
        """Register ``factory`` under ``provider``, replacing any earlier entry."""
        self.factories[provider] = factory

    def create(self, provider: str, cfg: ProviderConfig) -> Model:
        """Build the model for ``provider``, defaulting to :data:`DEFAULT_PROVIDER`."""
        if provider == "":
            provider = DEFAULT_PROVIDER
        factory = self.factories.get(provider)
        if factory is None:
            raise ValueError("unknown llm provider: " + provider)
        return factory(cfg)


def new_model_registry() -> ModelRegistry:
    """An empty registry."""
    return ModelRegistry()


def new_default_model_registry() -> ModelRegistry:
    """A registry holding the built-in ``deepseek``, ``openai``, and ``claude`` providers.

    Each factory returns a deferred model; provider imports happen on its first
    request rather than while the terminal starts.
    """
    registry = new_model_registry()
    registry.register_configured("deepseek", _deferred_deepseek)
    registry.register_configured("openai", _deferred_openai)
    registry.register_configured("claude", _deferred_claude)
    return registry


@functools.cache
def _default_registry() -> ModelRegistry:
    """The process-wide registry of built-in providers."""
    return new_default_model_registry()


def new_model(provider: str, cfg: ProviderConfig) -> Model:
    """Build a model from the default registry."""
    return _default_registry().create(provider, cfg)


def model_display_name(provider: str, cfg: ProviderConfig) -> str:
    """The model name to show, falling back to the provider's default model."""
    if cfg.model != "":
        return cfg.model
    if provider == "":
        provider = DEFAULT_PROVIDER
    match provider:
        case "deepseek":
            return "deepseek-reasoner"
        case "openai":
            return "gpt-4o"
        case "claude":
            return "claude-3-7-sonnet-20250219"
        case _:
            return provider
