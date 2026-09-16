"""OpenAI-compatible adapter.

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
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_USER,
    Attachment,
    Message,
    StreamChunk,
    ToolSpec,
)

_Handler = Callable[[httpx2.Request], httpx2.Response]


def _chunk(delta: dict[str, Any], finish_reason: str | None = None) -> str:
    choice: dict[str, Any] = {"index": 0, "delta": delta}
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
    return json.dumps(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "test-model",
            "choices": [choice],
        }
    )


def _stream(*chunks: str) -> bytes:
    return ("".join(f"data: {chunk}\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode("utf-8")


_OK_STREAM = _stream(_chunk({"role": "assistant", "content": "ok"}, "stop"))
_CONTENT_STREAM = _stream(
    _chunk({"role": "assistant", "content": "hello from llm"}),
    _chunk({}, "stop"),
)


def _response(body: bytes) -> httpx2.Response:
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def _discard(chunk: StreamChunk) -> None:
    """An ``on_stream_chunk`` that ignores its argument."""


@contextlib.asynccontextmanager
async def _transport(monkeypatch: pytest.MonkeyPatch, handler: _Handler) -> AsyncGenerator[None]:
    """Route every request the adapter makes to ``handler``."""
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    monkeypatch.setattr(_openai_module, "http_client", lambda: client)
    try:
        yield
    finally:
        await client.aclose()


def _config() -> llm.ProviderConfig:
    return llm.ProviderConfig(base_url="https://openai.test", api_key="test-key", model="test-model")


def _body(request: httpx2.Request) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(request.content))


@pytest.mark.asyncio
async def test_openai_model_sends_chat_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured.update(_body(request))
        return _response(_CONTENT_STREAM)

    spec = ToolSpec(
        name="bash",
        description="Run a bash command after user approval.",
        risky=True,
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    )

    async with _transport(monkeypatch, handler):
        response = await llm.new_open_ai(_config()).next(
            RunContext(),
            [Message(role=ROLE_USER, content="hi")],
            [spec],
            _discard,
        )

    assert response.content == "hello from llm"
    assert captured["path"] == "/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["model"] == "test-model"
    assert [(message["role"], message["content"]) for message in captured["messages"]] == [("user", "hi")]
    tools = captured["tools"]
    assert len(tools) == 1
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "bash"


@pytest.mark.asyncio
async def test_openai_model_sends_system_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(_body(request))
        return _response(_OK_STREAM)

    async with _transport(monkeypatch, handler):
        await llm.new_open_ai(_config()).next(
            RunContext(),
            [
                Message(role=ROLE_SYSTEM, content="project instructions"),
                Message(role=ROLE_USER, content="hi"),
            ],
            [],
            _discard,
        )

    messages = captured["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "project instructions"


@pytest.mark.asyncio
async def test_openai_model_sends_image_and_file_attachments(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(_body(request))
        return _response(_OK_STREAM)

    async with _transport(monkeypatch, handler):
        await llm.new_open_ai(_config()).next(
            RunContext(),
            [
                Message(
                    role=ROLE_USER,
                    content="inspect",
                    attachments=(
                        Attachment(name="pixel.png", mime="image/png", data="aW1hZ2U="),
                        Attachment(name="note.txt", mime="text/plain", data="dGV4dA=="),
                    ),
                )
            ],
            [],
            _discard,
        )

    content = captured["messages"][0]["content"]
    assert len(content) == 3
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[2]["file"]["filename"] == "note.txt"
    assert content[2]["file"]["file_data"] == "dGV4dA=="


@pytest.mark.asyncio
async def test_openai_model_uses_sdk_default_base_url_when_config_base_url_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["host"] = request.url.host
        captured["path"] = request.url.path
        return _response(_OK_STREAM)

    async with _transport(monkeypatch, handler):
        response = await llm.new_open_ai(llm.ProviderConfig(api_key="test-key", model="test-model")).next(
            RunContext(), [Message(role=ROLE_USER, content="hi")], [], _discard
        )

    assert captured["host"] == "api.openai.com"
    assert captured["path"] == "/v1/chat/completions"
    assert response.content == "ok"


@pytest.mark.asyncio
async def test_openai_model_replays_reasoning_content(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(_body(request))
        return _response(
            _stream(
                _chunk({"role": "assistant", "content": "final", "reasoning_content": "thinking"}),
                _chunk({}, "stop"),
            )
        )

    async with _transport(monkeypatch, handler):
        response = await llm.new_open_ai(_config()).next(
            RunContext(),
            [
                Message(role=ROLE_ASSISTANT, content="old", reasoning_content="old thinking"),
                Message(role=ROLE_USER, content="next"),
            ],
            [],
            _discard,
        )

    assert response.reasoning_content == "thinking"
    assert captured["messages"][0]["reasoning_content"] == "old thinking"


@pytest.mark.asyncio
async def test_openai_model_leaves_tool_risk_to_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _stream(
        _chunk(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command":"ls"}'},
                    }
                ],
            },
            "tool_calls",
        )
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        return _response(body)

    async with _transport(monkeypatch, handler):
        response = await llm.new_open_ai(_config()).next(
            RunContext(),
            [Message(role=ROLE_USER, content="list files")],
            [ToolSpec(name="bash", risky=True)],
            _discard,
        )

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "bash"


@pytest.mark.asyncio
async def test_openai_model_returns_all_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _stream(
        _chunk(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "first", "arguments": "{}"},
                    },
                    {
                        "index": 1,
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "second", "arguments": "{}"},
                    },
                ],
            },
            "tool_calls",
        )
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        return _response(body)

    async with _transport(monkeypatch, handler):
        response = await llm.new_open_ai(_config()).next(
            RunContext(),
            [Message(role=ROLE_USER, content="use tools")],
            [ToolSpec(name="first"), ToolSpec(name="second", risky=True)],
            _discard,
        )

    assert [call.name for call in response.tool_calls] == ["first", "second"]


@pytest.mark.asyncio
async def test_openai_model_fails_on_truncated_tool_call(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _stream(
        _chunk(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": '{"path":"main.py","content":"import'},
                    }
                ],
            },
            "length",
        )
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        return _response(body)

    async with _transport(monkeypatch, handler):
        with pytest.raises(RuntimeError, match="truncated"):
            await llm.new_open_ai(_config()).next(
                RunContext(),
                [Message(role=ROLE_USER, content="write the file")],
                [ToolSpec(name="write_file", risky=True)],
                _discard,
            )


@pytest.mark.asyncio
async def test_openai_model_reports_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    usage_chunk = json.dumps(
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        }
    )
    body = _stream(_chunk({"content": "ok"}, "stop"), usage_chunk)

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.update(_body(request))
        return _response(body)

    async with _transport(monkeypatch, handler):
        response = await llm.new_open_ai(_config()).next(
            RunContext(),
            [Message(role=ROLE_USER, content="hi")],
            [],
            _discard,
        )

    assert captured["stream_options"]["include_usage"] is True
    assert response.usage is not None
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 7
    assert response.usage.total_tokens == 18


@pytest.mark.asyncio
async def test_openai_model_uses_environment_api_key_when_config_key_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty config key must not shadow the environment API key.

    The SDK sends the request with no ``Authorization`` header when no key is
    configured anywhere, but it refuses to build a client without credentials. So
    the property this pins is that an empty ``api_key`` leaves the variable unset
    for the SDK, so the environment fallback still applies rather than being
    shadowed by an empty value.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["authorization"] = request.headers.get("authorization")
        return _response(_OK_STREAM)

    async with _transport(monkeypatch, handler):
        await llm.new_open_ai(llm.ProviderConfig(model="test-model")).next(
            RunContext(),
            [Message(role=ROLE_USER, content="hi")],
            [],
            _discard,
        )

    assert captured["authorization"] == "Bearer env-key"
