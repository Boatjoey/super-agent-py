"""Provider registry and factory.

A provider name maps to a factory that builds a
:class:`~super_agent.runtime.protocol.types.Model`. The registry of built-in
providers is built on the first :func:`NewModel` call rather than at import, so
that importing a single provider module never depends on the package ``__init__``.
"""

from __future__ import annotations

import dataclasses
import functools
from collections.abc import Callable
from typing import Final

from super_agent.jsonutil import json_field
from super_agent.runtime.protocol.types import Model

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


class ModelRegistry:
    """A provider-name to model-factory table."""

    def __init__(self) -> None:
        self.factories: dict[str, ModelFactory] = {}

    def register_configured(self, provider: str, factory: ModelFactory) -> None:
        """Register ``factory`` under ``provider``, replacing any earlier entry."""
        self.factories[provider] = factory

    def create(self, provider: str, cfg: ProviderConfig) -> Model:
        """Build the model for ``provider``, defaulting to :data:`DefaultProvider`."""
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

    The provider modules are imported here rather than at module scope: each of
    them imports :class:`ProviderConfig` from this module, so a module-level
    import would be circular.
    """
    from super_agent.llm.claude import new_claude
    from super_agent.llm.deepseek import new_deep_seek
    from super_agent.llm.openai import new_open_ai

    registry = new_model_registry()
    registry.register_configured("deepseek", new_deep_seek)
    registry.register_configured("openai", new_open_ai)
    registry.register_configured("claude", new_claude)
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
