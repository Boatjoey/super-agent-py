"""Provider adapters.

Claude, OpenAI, and DeepSeek behind the ``runtime/protocol`` ``Model`` port, plus
the registry that turns a provider name into one of them. The llm package
surface: everything callers write as ``llm.X`` is re-exported here.
"""

from __future__ import annotations

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
