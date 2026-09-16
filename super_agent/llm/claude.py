"""Anthropic Claude adapter.

The Messages API has no ``system`` role, so system messages are lifted into the
top-level ``system`` field and the rest of the conversation is translated into
content blocks: tool results become ``user`` messages carrying ``tool_result``
blocks, and adjacent same-role messages are merged because the API rejects two in
a row.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
from collections.abc import Callable
from typing import Any, cast

import anthropic
from anthropic.types import (
    InputJSONDelta,
    MessageParam,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    RawMessageDeltaEvent,
    RawMessageStartEvent,
    TextBlockParam,
    TextDelta,
    ThinkingDelta,
    ToolUnionParam,
    ToolUseBlock,
)
from anthropic.types.message_create_params import MessageCreateParamsStreaming

from super_agent.errors import Cancelled
from super_agent.llm import openai as _openai
from super_agent.llm.factory import ProviderConfig
from super_agent.runtime.protocol.run_context import DEFAULT_CANCEL_REASON, RunContext
from super_agent.runtime.protocol.types import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ModelResponse,
    StreamChunk,
    ToolCall,
    ToolSpec,
    Usage,
)

#: Raised on ``stop_reason=max_tokens`` rather than handing a half-emitted
#: ``input_json_delta`` to the executor as if it were valid JSON.
TRUNCATED_ERROR = (
    "llm output truncated by the token limit (stop_reason=max_tokens); "
    "raise the model's max output or shorten the conversation"
)


class ClaudeModel:
    """A chat model reached over the Anthropic Messages API."""

    def __init__(self, client: anthropic.AsyncAnthropic, model: str) -> None:
        self.client = client
        self.model = model

    async def next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        system, conversation = split_system_messages(messages)
        params: MessageCreateParamsStreaming = {
            "model": self.model,
            "max_tokens": 8192,
            "messages": to_claude_messages(conversation),
            "stream": True,
        }
        if system != "":
            params["system"] = [TextBlockParam(type="text", text=system)]
        if len(tools) > 0:
            params["tools"] = to_claude_tools(tools)

        final_answer = ""
        reasoning_content = ""
        tool_calls: list[ToolCall] = []
        current_tool_use_id = ""
        current_tool_use_name = ""
        current_tool_use_input = ""
        stop_reason: str | None = None
        input_tokens = 0
        output_tokens = 0

        try:
            stream = await self.client.messages.create(**params)
            async for event in stream:
                if isinstance(event, RawMessageStartEvent):
                    input_tokens = event.message.usage.input_tokens
                elif isinstance(event, RawContentBlockStartEvent):
                    block = event.content_block
                    if isinstance(block, ToolUseBlock):
                        current_tool_use_id = block.id
                        current_tool_use_name = block.name
                elif isinstance(event, RawContentBlockDeltaEvent):
                    delta = event.delta
                    if isinstance(delta, TextDelta):
                        final_answer += delta.text
                        on_stream_chunk(StreamChunk(content_delta=delta.text))
                    elif isinstance(delta, ThinkingDelta):
                        reasoning_content += delta.thinking
                        on_stream_chunk(StreamChunk(reasoning_content_delta=delta.thinking))
                    elif isinstance(delta, InputJSONDelta):
                        current_tool_use_input += delta.partial_json
                elif isinstance(event, RawContentBlockStopEvent):
                    if current_tool_use_id != "":
                        tool_calls.append(
                            ToolCall(
                                id=current_tool_use_id,
                                name=current_tool_use_name,
                                input=current_tool_use_input,
                            )
                        )
                        current_tool_use_id = ""
                        current_tool_use_name = ""
                        current_tool_use_input = ""
                elif isinstance(event, RawMessageDeltaEvent):
                    stop_reason = event.delta.stop_reason
                    output_tokens = event.usage.output_tokens
        except asyncio.CancelledError as err:
            raise Cancelled(DEFAULT_CANCEL_REASON) from err

        if stop_reason == "max_tokens":
            raise RuntimeError(TRUNCATED_ERROR)

        usage: Usage | None = None
        if input_tokens > 0 or output_tokens > 0:
            usage = Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            )
        return ModelResponse(
            content=final_answer,
            reasoning_content=reasoning_content,
            tool_calls=tuple(tool_calls),
            usage=usage,
        )


def new_claude(cfg: ProviderConfig) -> ClaudeModel:
    """A Claude model with the ``claude-3-7-sonnet-20250219`` default."""
    cfg = _openai.with_defaults(cfg, ProviderConfig(model="claude-3-7-sonnet-20250219"))
    return new_claude_model(cfg)


def new_claude_model(cfg: ProviderConfig) -> ClaudeModel:
    """Build the client and model. An empty key or base URL is left unset so the
    SDK's environment fallback (``ANTHROPIC_API_KEY``, ``ANTHROPIC_BASE_URL``) applies."""
    client = anthropic.AsyncAnthropic(
        http_client=_openai.http_client(),
        api_key=cfg.api_key or None,
        base_url=cfg.base_url or None,
    )
    return ClaudeModel(client=client, model=cfg.model)


def split_system_messages(messages: list[Message]) -> tuple[str, list[Message]]:
    """Lift system messages out, joining several with a blank line."""
    system = ""
    conversation: list[Message] = []
    for message in messages:
        if message.role == ROLE_SYSTEM:
            if system != "":
                system += "\n\n"
            system += message.content
            continue
        conversation.append(message)
    return system, conversation


def to_claude_messages(messages: list[Message]) -> list[MessageParam]:
    """Translate protocol messages into Messages API message params."""
    result: list[MessageParam] = []
    for message in messages:
        if message.role == ROLE_USER:
            blocks: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
            for attachment in message.attachments:
                blocks.extend(_attachment_blocks(attachment.name, attachment.mime, attachment.data))
            result.append(cast(MessageParam, {"role": "user", "content": blocks}))
        elif message.role == ROLE_ASSISTANT:
            assistant_blocks: list[dict[str, Any]] = []
            if message.content != "":
                assistant_blocks.append({"type": "text", "text": message.content})
            if message.tool_calls:
                for call in message.tool_calls:
                    try:
                        parsed: Any = json.loads(call.input)
                    except json.JSONDecodeError:
                        parsed = {}
                    assistant_blocks.append({"type": "tool_use", "id": call.id, "name": call.name, "input": parsed})
            if assistant_blocks:
                result.append(cast(MessageParam, {"role": "assistant", "content": assistant_blocks}))
        elif message.role == ROLE_TOOL:
            tool_result = {
                "type": "tool_result",
                "tool_use_id": message.tool_call_id,
                "content": message.content,
            }
            result.append(cast(MessageParam, {"role": "user", "content": [tool_result]}))
    return merge_adjacent_messages(result)


def _attachment_blocks(name: str, mime: str, data: str) -> list[dict[str, Any]]:
    """Translate one attachment into content blocks."""
    if mime.startswith("image/"):
        return [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}]
    if mime == "application/pdf":
        return [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": data},
            }
        ]
    if mime.startswith("text/"):
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return []
        return [{"type": "text", "text": "Attachment " + name + ":\n" + decoded.decode("utf-8", errors="replace")}]
    return [{"type": "text", "text": "Attached file: " + name + " (" + mime + ")"}]


def merge_adjacent_messages(messages: list[MessageParam]) -> list[MessageParam]:
    """Join runs of same-role messages, concatenating their content blocks."""
    if len(messages) == 0:
        return messages
    merged: list[MessageParam] = []
    current = messages[0]
    for message in messages[1:]:
        if current["role"] == message["role"]:
            content = list(cast("list[Any]", current["content"]))
            content.extend(cast("list[Any]", message["content"]))
            current = cast(MessageParam, {"role": current["role"], "content": content})
        else:
            merged.append(current)
            current = message
    merged.append(current)
    return merged


def to_claude_tools(tools: list[ToolSpec]) -> list[ToolUnionParam]:
    """Advertise ``tools``, passing the whole schema through.

    Known keys populate the typed fields and every other top-level keyword
    (``$defs``, ``$ref``, ``oneOf``, …) rides along, because cherry-picking
    ``properties``/``required`` silently degrades MCP schemas that rely on shared
    definitions. The root ``type`` is always ``object``, as Anthropic requires.
    """
    result: list[ToolUnionParam] = []
    for tool in tools:
        schema: dict[str, Any] = dict(tool.parameters) if tool.parameters is not None else {}
        schema["type"] = "object"
        result.append(
            cast(
                ToolUnionParam,
                {"name": tool.name, "description": tool.description, "input_schema": schema},
            )
        )
    return result
