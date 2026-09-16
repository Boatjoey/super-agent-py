"""OpenAI-compatible chat adapter.

One implementation serves both OpenAI and DeepSeek: :func:`NewDeepSeek` is the
same model with a different base URL and without an explicit usage request. The
adapter streams chat completions and accumulates deltas itself so the merge rules
— tool-call fragments keyed by ``index`` with ``function.arguments`` concatenated
in order — are visible in one place instead of hidden in an SDK helper.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from typing import Any, cast

import httpx2
from openai import AsyncOpenAI, AsyncStream, Omit, omit
from openai.types.chat import (
    ChatCompletionChunk,
    ChatCompletionMessageParam,
    ChatCompletionStreamOptionsParam,
    ChatCompletionToolParam,
)
from openai.types.chat.chat_completion_chunk import ChoiceDelta

from super_agent.errors import Cancelled
from super_agent.llm.factory import ProviderConfig
from super_agent.runtime.protocol.run_context import DEFAULT_CANCEL_REASON, RunContext
from super_agent.runtime.protocol.types import (
    Message,
    ModelResponse,
    RoleAssistant,
    RoleSystem,
    RoleTool,
    StreamChunk,
    ToolCall,
    ToolSpec,
    Usage,
)

#: The connection stays open for as long as the provider keeps sending; two
#: minutes bounds how long a stalled connection may hang.
STREAM_TIMEOUT_SECONDS = 120.0

#: Raised on ``finish_reason=length`` rather than handing a half-emitted tool-call
#: argument string to the executor.
TRUNCATED_ERROR = (
    "llm output truncated by the token limit (finish_reason=length); "
    "raise the model's max output or shorten the conversation"
)


def http_client() -> httpx2.AsyncClient:
    """Build the HTTP client the provider SDK streams through.

    This function is the seam tests monkeypatch to return a client backed by
    ``httpx2.MockTransport``.
    """
    return httpx2.AsyncClient(timeout=httpx2.Timeout(STREAM_TIMEOUT_SECONDS))


def with_defaults(cfg: ProviderConfig, defaults: ProviderConfig) -> ProviderConfig:
    """Fill every empty field of ``cfg`` from ``defaults``."""
    return ProviderConfig(
        BaseURL=cfg.BaseURL or defaults.BaseURL,
        APIKey=cfg.APIKey or defaults.APIKey,
        Model=cfg.Model or defaults.Model,
    )


@dataclasses.dataclass
class _ToolCallBuilder:
    """One streamed tool call being assembled across chunks."""

    id: str = ""
    name: str = ""
    arguments: str = ""


class OpenAIModel:
    """A chat model reached over the OpenAI chat-completions API."""

    def __init__(self, client: AsyncOpenAI, model: str, usage: bool) -> None:
        self.client = client
        self.model = model
        self.usage = usage

    async def Next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        content = ""
        reasoning = ""
        refusal = ""
        finish_reason: str | None = None
        saw_choice = False
        stream_usage: Usage | None = None
        builders: dict[int, _ToolCallBuilder] = {}

        try:
            stream = await self._create_stream(messages, tools)
            async for chunk in stream:
                if chunk.usage is not None:
                    stream_usage = Usage(
                        InputTokens=chunk.usage.prompt_tokens,
                        OutputTokens=chunk.usage.completion_tokens,
                        TotalTokens=chunk.usage.total_tokens,
                    )
                for choice in chunk.choices:
                    saw_choice = True
                    delta = choice.delta
                    reasoning_delta = _reasoning_delta(delta)
                    if reasoning_delta != "":
                        reasoning += reasoning_delta
                    if delta.content:
                        content += delta.content
                    if delta.refusal:
                        refusal += delta.refusal
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
                    for call in delta.tool_calls or []:
                        builder = builders.setdefault(call.index, _ToolCallBuilder())
                        if call.id:
                            builder.id = call.id
                        if call.function is not None:
                            if call.function.name:
                                builder.name = call.function.name
                            if call.function.arguments:
                                builder.arguments += call.function.arguments
                    if delta.content or reasoning_delta:
                        on_stream_chunk(
                            StreamChunk(
                                ContentDelta=delta.content or "",
                                ReasoningContentDelta=reasoning_delta,
                            )
                        )
        except asyncio.CancelledError as err:
            raise Cancelled(DEFAULT_CANCEL_REASON) from err

        if not saw_choice:
            raise RuntimeError("llm returned no choices")
        if finish_reason == "length":
            raise RuntimeError(TRUNCATED_ERROR)
        if refusal != "":
            raise RuntimeError("llm refused the request: " + refusal)

        calls = tuple(
            ToolCall(ID=builder.id, Name=builder.name, Input=builder.arguments)
            for _, builder in sorted(builders.items())
        )
        usage: Usage | None = None
        if stream_usage is not None and (stream_usage.InputTokens > 0 or stream_usage.OutputTokens > 0):
            usage = stream_usage
        return ModelResponse(Content=content, ReasoningContent=reasoning, ToolCalls=calls, Usage=usage)

    async def _create_stream(self, messages: list[Message], tools: list[ToolSpec]) -> AsyncStream[ChatCompletionChunk]:
        tool_params = to_openai_tools(tools)
        stream_options: ChatCompletionStreamOptionsParam | Omit = (
            ChatCompletionStreamOptionsParam(include_usage=True) if self.usage else omit
        )
        return await self.client.chat.completions.create(
            model=self.model,
            messages=to_openai_messages(messages),
            tools=tool_params if tool_params else omit,
            stream=True,
            stream_options=stream_options,
        )


def NewOpenAI(cfg: ProviderConfig) -> OpenAIModel:
    """An OpenAI model with the ``gpt-4o`` default."""
    cfg = with_defaults(cfg, ProviderConfig(Model="gpt-4o"))
    return new_openai_model(cfg, True)


def new_openai_model(cfg: ProviderConfig, request_usage: bool) -> OpenAIModel:
    """Build the client and model. An empty key or base URL is left unset so the
    SDK's environment fallback (``OPENAI_API_KEY``, ``OPENAI_BASE_URL``) applies."""
    client = AsyncOpenAI(
        http_client=http_client(),
        default_headers={"X-Title": "SuperAgent"},
        api_key=cfg.APIKey or None,
        base_url=cfg.BaseURL or None,
    )
    return OpenAIModel(client=client, model=cfg.Model, usage=request_usage)


def to_openai_tools(tools: list[ToolSpec]) -> list[ChatCompletionToolParam]:
    """Advertise ``tools`` as function tools."""
    params: list[ChatCompletionToolParam] = []
    for tool in tools:
        function: dict[str, Any] = {"name": tool.Name, "description": tool.Description}
        if tool.Parameters is not None:
            function["parameters"] = tool.Parameters
        params.append(cast(ChatCompletionToolParam, {"type": "function", "function": function}))
    return params


def to_openai_messages(messages: list[Message]) -> list[ChatCompletionMessageParam]:
    """Translate protocol messages into chat-completion messages."""
    params: list[ChatCompletionMessageParam] = []
    for message in messages:
        if message.Role == RoleSystem:
            params.append(cast(ChatCompletionMessageParam, {"role": "system", "content": message.Content}))
        elif message.Role == RoleAssistant:
            params.append(
                cast(
                    ChatCompletionMessageParam,
                    assistant_message(message.Content, message.ReasoningContent, message.ToolCalls),
                )
            )
        elif message.Role == RoleTool:
            params.append(
                cast(
                    ChatCompletionMessageParam,
                    {"role": "tool", "content": message.Content, "tool_call_id": message.ToolCallID},
                )
            )
        else:
            params.append(cast(ChatCompletionMessageParam, openai_user_message(message)))
    return params


def assistant_message(content: str, reasoning_content: str, tool_calls: tuple[ToolCall, ...] | None) -> dict[str, Any]:
    """Build the assistant message, replaying reasoning and tool calls.

    ``reasoning_content`` must be passed back for DeepSeek thinking mode with
    tools: the API rejects the turn without it. Plain OpenAI tolerates the
    unknown message field.
    """
    result: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content != "":
        result["reasoning_content"] = reasoning_content
    if tool_calls:
        result["tool_calls"] = [
            {
                "id": call.ID,
                "type": "function",
                "function": {"name": call.Name, "arguments": call.Input},
            }
            for call in tool_calls
        ]
    return result


def openai_user_message(message: Message) -> dict[str, Any]:
    """Build a user message, inlining attachments as content parts."""
    if len(message.Attachments) == 0:
        return {"role": "user", "content": message.Content}
    parts: list[dict[str, Any]] = [{"type": "text", "text": message.Content}]
    for attachment in message.Attachments:
        if attachment.MIME.startswith("image/"):
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:" + attachment.MIME + ";base64," + attachment.Data,
                        "detail": "auto",
                    },
                }
            )
        else:
            parts.append(
                {
                    "type": "file",
                    "file": {"file_data": attachment.Data, "filename": attachment.Name},
                }
            )
    return {"role": "user", "content": parts}


def _reasoning_delta(delta: ChoiceDelta) -> str:
    """Decode the reasoning field names OpenAI-compatible providers use.

    DeepSeek emits ``reasoning_content``, other gateways ``reasoning`` or
    ``thinking``; none of them is modelled by the typed SDK delta.
    """
    extras = delta.model_extra or {}
    for key in ("reasoning_content", "reasoning", "thinking"):
        value = extras.get(key)
        if isinstance(value, str) and value != "":
            return value
    return ""
