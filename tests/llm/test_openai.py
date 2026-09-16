"""OpenAI-compatible adapter, ported from ``tests/llm/openai_test.go``.

The Go tests stand up an ``httptest`` server and read the request body in the
handler. These use ``httpx2.MockTransport`` instead: the handler sees the same
request the SDK would have sent, so the assertions are on the JSON body the
adapter produced and on the parsed result, and nothing touches the network.
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
    Attachment,
    Message,
    RoleAssistant,
    RoleSystem,
    RoleUser,
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
    return llm.ProviderConfig(BaseURL="https://openai.test", APIKey="test-key", Model="test-model")


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
        Name="bash",
        Description="Run a bash command after user approval.",
        Risky=True,
        Parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    )

    async with _transport(monkeypatch, handler):
        response = await llm.NewOpenAI(_config()).Next(
            RunContext(),
            [Message(Role=RoleUser, Content="hi")],
            [spec],
            _discard,
        )

    assert response.Content == "hello from llm"
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
        await llm.NewOpenAI(_config()).Next(
            RunContext(),
            [
                Message(Role=RoleSystem, Content="project instructions"),
                Message(Role=RoleUser, Content="hi"),
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
        await llm.NewOpenAI(_config()).Next(
            RunContext(),
            [
                Message(
                    Role=RoleUser,
                    Content="inspect",
                    Attachments=(
                        Attachment(Name="pixel.png", MIME="image/png", Data="aW1hZ2U="),
                        Attachment(Name="note.txt", MIME="text/plain", Data="dGV4dA=="),
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
        response = await llm.NewOpenAI(llm.ProviderConfig(APIKey="test-key", Model="test-model")).Next(
            RunContext(), [Message(Role=RoleUser, Content="hi")], [], _discard
        )

    assert captured["host"] == "api.openai.com"
    assert captured["path"] == "/v1/chat/completions"
    assert response.Content == "ok"


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
        response = await llm.NewOpenAI(_config()).Next(
            RunContext(),
            [
                Message(Role=RoleAssistant, Content="old", ReasoningContent="old thinking"),
                Message(Role=RoleUser, Content="next"),
            ],
            [],
            _discard,
        )

    assert response.ReasoningContent == "thinking"
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
        response = await llm.NewOpenAI(_config()).Next(
            RunContext(),
            [Message(Role=RoleUser, Content="list files")],
            [ToolSpec(Name="bash", Risky=True)],
            _discard,
        )

    assert len(response.ToolCalls) == 1
    assert response.ToolCalls[0].Name == "bash"


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
        response = await llm.NewOpenAI(_config()).Next(
            RunContext(),
            [Message(Role=RoleUser, Content="use tools")],
            [ToolSpec(Name="first"), ToolSpec(Name="second", Risky=True)],
            _discard,
        )

    assert [call.Name for call in response.ToolCalls] == ["first", "second"]


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
                        "function": {"name": "write_file", "arguments": '{"path":"main.go","content":"package'},
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
            await llm.NewOpenAI(_config()).Next(
                RunContext(),
                [Message(Role=RoleUser, Content="write the file")],
                [ToolSpec(Name="write_file", Risky=True)],
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
        response = await llm.NewOpenAI(_config()).Next(
            RunContext(),
            [Message(Role=RoleUser, Content="hi")],
            [],
            _discard,
        )

    assert captured["stream_options"]["include_usage"] is True
    assert response.Usage is not None
    assert response.Usage.InputTokens == 11
    assert response.Usage.OutputTokens == 7
    assert response.Usage.TotalTokens == 18


@pytest.mark.asyncio
async def test_openai_model_uses_environment_api_key_when_config_key_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replaces ``TestOpenAIModelSkipsAuthHeaderWithoutAPIKey``.

    Go's SDK sends the request with no ``Authorization`` header when no key is
    configured anywhere. The Python SDK refuses to build a client without
    credentials, so the port pins the property that matters instead: an empty
    ``APIKey`` leaves the variable unset for the SDK, so the environment
    fallback still applies rather than being shadowed by an empty value.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    captured: dict[str, Any] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured["authorization"] = request.headers.get("authorization")
        return _response(_OK_STREAM)

    async with _transport(monkeypatch, handler):
        await llm.NewOpenAI(llm.ProviderConfig(Model="test-model")).Next(
            RunContext(),
            [Message(Role=RoleUser, Content="hi")],
            [],
            _discard,
        )

    assert captured["authorization"] == "Bearer env-key"
