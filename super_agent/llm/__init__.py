"""Provider adapters.

Claude, OpenAI, and DeepSeek behind the ``runtime/protocol`` ``Model`` port, plus
the registry that turns a provider name into one of them. Python splits a Go
package across files, so this module stands in for the package namespace:
everything Go callers write as ``llm.X`` is re-exported here.
"""

from __future__ import annotations

from super_agent.llm.claude import (
    ClaudeModel as ClaudeModel,
    NewClaude as NewClaude,
    merge_adjacent_messages as merge_adjacent_messages,
    new_claude_model as new_claude_model,
    split_system_messages as split_system_messages,
    to_claude_messages as to_claude_messages,
    to_claude_tools as to_claude_tools,
)
from super_agent.llm.deepseek import NewDeepSeek as NewDeepSeek
from super_agent.llm.factory import (
    DefaultProvider as DefaultProvider,
    ModelDisplayName as ModelDisplayName,
    ModelFactory as ModelFactory,
    ModelRegistry as ModelRegistry,
    NewDefaultModelRegistry as NewDefaultModelRegistry,
    NewModel as NewModel,
    NewModelRegistry as NewModelRegistry,
    ProviderConfig as ProviderConfig,
)
from super_agent.llm.openai import (
    NewOpenAI as NewOpenAI,
    OpenAIModel as OpenAIModel,
    assistant_message as assistant_message,
    http_client as http_client,
    new_openai_model as new_openai_model,
    openai_user_message as openai_user_message,
    to_openai_messages as to_openai_messages,
    to_openai_tools as to_openai_tools,
    with_defaults as with_defaults,
)
