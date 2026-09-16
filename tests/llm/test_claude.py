"""Claude adapter.

These use ``httpx2.MockTransport``: the handler sees the same request the SDK
would have sent, so the assertions are on the JSON body the adapter produced and
on the parsed result, and nothing touches the network.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncGenerator, Callable
from typing import Any, cast

import httpx2
import pytest

from super_agent import llm
from super_agent.llm import openai as _openai_module
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import (
    ROLE_SYSTEM,
    ROLE_USER,
    Attachment,
    Message,
    StreamChunk,
    ToolSpec,
)

_Handler = Callable[[httpx2.Request], httpx2.Response]

#: The Anthropic SDK's SSE decoder drops events that carry no ``event:`` line,
#: so every canned body names the event as well as the JSON type.
_MESSAGE_START = (
    '{"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant",'
    '"model":"test-model","content":[],"stop_reason":null,"stop_sequence":null,'
    '"usage":{"input_tokens":1,"output_tokens":0}}}'
)
_SIMPLE_STREAM = (
    ("message_start", _MESSAGE_START),
    ("content_block_start", '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":"ok"}}'),
    ("message_stop", '{"type":"message_stop"}'),
)
_OK_STREAM = (
    ("message_start", _MESSAGE_START),
    ("content_block_start", '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}'),
    ("content_block_delta", '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}'),
    ("content_block_stop", '{"type":"content_block_stop","index":0}'),
    (
        "message_delta",
        '{"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":1}}',
    ),
    ("message_stop", '{"type":"message_stop"}'),
)
_MAX_TOKENS_STREAM = (
    (
        "message_start",
        '{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"m",'
        '"content":[],"usage":{"input_tokens":5,"output_tokens":0}}}',
    ),
    (
        "content_block_start",
        '{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1",'
        '"name":"write_file","input":{}}}',
    ),
    (
        "content_block_delta",
        '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta",'
        '"partial_json":"{\\"path\\":\\"main.py\\""}}',
    ),
    (
        "message_delta",
        '{"type":"message_delta","delta":{"stop_reason":"max_tokens","stop_sequence":null},'
        '"usage":{"output_tokens":8192}}',
    ),
    ("message_stop", '{"type":"message_stop"}'),
)
_USAGE_STREAM = (
    (
        "message_start",
        '{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"m",'
        '"content":[],"usage":{"input_tokens":11,"output_tokens":0}}}',
    ),
    ("content_block_start", '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}'),
    ("content_block_delta", '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}'),
    ("content_block_stop", '{"type":"content_block_stop","index":0}'),
    (
        "message_delta",
        '{"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":7}}',
    ),
    ("message_stop", '{"type":"message_stop"}'),
)


def _sse(*events: tuple[str, str]) -> bytes:
    """Encode ``(event name, data)`` pairs the way the Anthropic wire does."""
    return "".join(f"event: {name}\ndata: {data}\n\n" for name, data in events).encode("utf-8")


def _response(body: bytes) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def _discard(chunk: StreamChunk) -> None:
    """An ``on_stream_chunk`` that ignores its argument."""


@contextlib.asynccontextmanager
async def _transport(monkeypatch: pytest.MonkeyPatch, handler: _Handler) -> AsyncGenerator[None]:
    """Route every request the adapter makes to ``handler``.

    ``llm/claude.py`` reaches the HTTP client through ``llm/openai.py``, so
    patching that one function covers both adapters.
    """
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(_openai_module, "http_client", lambda: client)
    try:
        yield
    finally:
        await client.aclose()


def _model(cfg: llm.ProviderConfig) -> llm.ClaudeModel:
    return llm.new_claude(cfg)


def _config() -> llm.ProviderConfig:
    return llm.ProviderConfig(base_url="https://claude.test", api_key="test-key", model="test-model")


def _body(request: httpx2.Request) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(request.content))


@pytest.mark.asyncio
async def test_claude_model_sends_system_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(_body(request))
        return _response(_sse(*_OK_STREAM))

    async with _transport(monkeypatch, handler):
        await _model(_config()).next(
            RunContext(),
            [
                Message(role=ROLE_SYSTEM, content="project instructions"),
                Message(role=ROLE_USER, content="hi"),
            ],
            [],
            _discard,
        )

    assert captured["system"] == [{"type": "text", "text": "project instructions"}]
    assert [message["role"] for message in captured["messages"]] == ["user"]


@pytest.mark.asyncio
async def test_claude_model_sends_image_attachment(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(_body(request))
        return _response(_sse(*_SIMPLE_STREAM))

    async with _transport(monkeypatch, handler):
        await _model(_config()).next(
            RunContext(),
            [
                Message(
                    role=ROLE_USER,
                    content="inspect",
                    attachments=(Attachment(name="pixel.png", mime="image/png", data="aW1hZ2U="),),
                )
            ],
            [],
            _discard,
        )

    types = [block["type"] for block in captured["messages"][0]["content"]]
    assert types == ["text", "image"]


@pytest.mark.asyncio
async def test_claude_model_fails_on_max_tokens_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _response(_sse(*_MAX_TOKENS_STREAM))

    async with _transport(monkeypatch, handler):
        with pytest.raises(RuntimeError, match="truncated"):
            await _model(_config()).next(
                RunContext(),
                [Message(role=ROLE_USER, content="write the file")],
                [ToolSpec(name="write_file", risky=True)],
                _discard,
            )


@pytest.mark.asyncio
async def test_claude_model_reports_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _response(_sse(*_USAGE_STREAM))

    async with _transport(monkeypatch, handler):
        response = await _model(_config()).next(
            RunContext(),
            [Message(role=ROLE_USER, content="hi")],
            [],
            _discard,
        )

    assert response.usage is not None
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7
    assert response.usage.total_tokens == 18


@pytest.mark.asyncio
async def test_claude_model_passes_through_schema_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(_body(request))
        return _response(_sse(*_SIMPLE_STREAM))

    spec = ToolSpec(
        name="mcp_tool",
        risky=True,
        parameters={
            "type": "object",
            "$defs": {
                "filters": {
                    "type": "object",
                    "properties": {"tag": {"type": "string"}},
                },
            },
            "properties": {"filter": {"$ref": "#/$defs/filters"}},
            "required": ["filter"],
        },
    )

    async with _transport(monkeypatch, handler):
        await _model(_config()).next(
            RunContext(),
            [Message(role=ROLE_USER, content="hi")],
            [spec],
            _discard,
        )

    tools = captured["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "mcp_tool"
    schema = tools[0]["input_schema"]
    assert "$defs" in schema, "schema extras must be passed through instead of dropped"
    assert "properties" in schema
