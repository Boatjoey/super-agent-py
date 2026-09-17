"""Provider adapters.

Claude, OpenAI, and DeepSeek behind the ``runtime/protocol`` ``Model`` port, plus
the registry that turns a provider name into one of them. The llm package
surface: everything callers write as ``llm.X`` is re-exported here. Provider
symbols are resolved lazily so importing the package does not load either SDK.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, Final

from super_agent.llm.factory import (
    DEFAULT_PROVIDER as DEFAULT_PROVIDER,
    ModelFactory as ModelFactory,
    ModelRegistry as ModelRegistry,
    ProviderConfig as ProviderConfig,
    model_display_name as model_display_name,
    new_default_model_registry as new_default_model_registry,
    new_model as new_model,
    new_model_registry as new_model_registry,
)

if TYPE_CHECKING:
    from super_agent.llm.claude import (
        ClaudeModel as ClaudeModel,
        merge_adjacent_messages as merge_adjacent_messages,
        new_claude as new_claude,
        new_claude_model as new_claude_model,
        split_system_messages as split_system_messages,
        to_claude_messages as to_claude_messages,
        to_claude_tools as to_claude_tools,
    )
    from super_agent.llm.deepseek import new_deep_seek as new_deep_seek
    from super_agent.llm.openai import (
        OpenAIModel as OpenAIModel,
        assistant_message as assistant_message,
        http_client as http_client,
        new_open_ai as new_open_ai,
        new_openai_model as new_openai_model,
        openai_user_message as openai_user_message,
        to_openai_messages as to_openai_messages,
        to_openai_tools as to_openai_tools,
        with_defaults as with_defaults,
    )

_LAZY_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "ClaudeModel": ("super_agent.llm.claude", "ClaudeModel"),
    "merge_adjacent_messages": ("super_agent.llm.claude", "merge_adjacent_messages"),
    "new_claude": ("super_agent.llm.claude", "new_claude"),
    "new_claude_model": ("super_agent.llm.claude", "new_claude_model"),
    "split_system_messages": ("super_agent.llm.claude", "split_system_messages"),
    "to_claude_messages": ("super_agent.llm.claude", "to_claude_messages"),
    "to_claude_tools": ("super_agent.llm.claude", "to_claude_tools"),
    "new_deep_seek": ("super_agent.llm.deepseek", "new_deep_seek"),
    "OpenAIModel": ("super_agent.llm.openai", "OpenAIModel"),
    "assistant_message": ("super_agent.llm.openai", "assistant_message"),
    "http_client": ("super_agent.llm.openai", "http_client"),
    "new_open_ai": ("super_agent.llm.openai", "new_open_ai"),
    "new_openai_model": ("super_agent.llm.openai", "new_openai_model"),
    "openai_user_message": ("super_agent.llm.openai", "openai_user_message"),
    "to_openai_messages": ("super_agent.llm.openai", "to_openai_messages"),
    "to_openai_tools": ("super_agent.llm.openai", "to_openai_tools"),
    "with_defaults": ("super_agent.llm.openai", "with_defaults"),
}


def __getattr__(name: str) -> Any:
    """Resolve the public provider surface without eager SDK imports."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(importlib.import_module(module_name), attribute_name)
    globals()[name] = value
    return value
