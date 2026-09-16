"""DeepSeek adapter.

DeepSeek speaks the OpenAI chat-completions protocol, so this is
:class:`~super_agent.llm.openai.OpenAIModel` with a DeepSeek base URL. Usage is
not requested explicitly: DeepSeek-compatible endpoints vary on ``stream_options``
support, and DeepSeek streams carry usage itself where available.
"""

from __future__ import annotations

from super_agent.llm.factory import ProviderConfig
from super_agent.llm.openai import OpenAIModel, new_openai_model, with_defaults


def new_deep_seek(cfg: ProviderConfig) -> OpenAIModel:
    """A DeepSeek model with the ``deepseek-reasoner`` default."""
    cfg = with_defaults(
        cfg,
        ProviderConfig(base_url="https://api.deepseek.com", model="deepseek-reasoner"),
    )
    return new_openai_model(cfg, False)
