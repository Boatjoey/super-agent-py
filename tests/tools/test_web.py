"""The network tools, plus the offline fetch cases.

No test here reaches the network: the fetch cases drive the tool through an
:class:`httpx.MockTransport`, and the rejection cases never get as far as a
connection.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest

from super_agent.runtime.protocol.run_context import live_context
from super_agent.runtime.protocol.types import ToolCall
from super_agent.tools import BrowserFetchTool, default_registry
from super_agent.tools.web import PinnedTransport, resolve_public_address
from tests.tools.test_workspace import workspace_for

PRIVATE_ADDRESS = "10.0.0.5"
PUBLIC_ADDRESS = "93.184.216.34"


async def answers_with_private(host: str, port: int) -> list[str]:
    """A stand-in resolver: every name answers with a private address."""
    return [PRIVATE_ADDRESS]


async def answers_with_public(host: str, port: int) -> list[str]:
    """A stand-in resolver: every name answers with one public address."""
    return [PUBLIC_ADDRESS]


def answers_ok(request: httpx.Request) -> httpx.Response:
    """A stand-in transport for the fetches that must never be sent."""
    return httpx.Response(200)


@pytest.mark.asyncio
async def test_browser_rejects_private_and_non_http_addresses() -> None:
    tool = BrowserFetchTool()
    for raw in (
        '{"url":"http://127.0.0.1/private"}',
        '{"url":"http://localhost/private"}',
        '{"url":"file:///etc/passwd"}',
    ):
        with pytest.raises((ValueError, RuntimeError)) as caught:
            await tool.run(live_context(), ToolCall(input=raw))
        message = str(caught.value)
        assert "blocked" in message or "HTTP(S)" in message


def test_web_tools_are_risky_network_tools(tmp_path: Path) -> None:
    seen = {
        spec.name: spec.risky
        for spec in default_registry(workspace_for(tmp_path)).specs()
        if spec.name in ("web_search", "browser_fetch")
    }

    assert seen["web_search"]
    assert seen["browser_fetch"]


@pytest.mark.asyncio
async def test_browser_fetch_extracts_text_and_resolves_links() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=("<html><body><script>bad()</script><p>Hello   world</p><a href='/next'>Next</a></body></html>"),
        )

    tool = BrowserFetchTool(transport=httpx.MockTransport(handler))

    output = await tool.run(live_context(), ToolCall(input='{"url":"https://example.com/page"}'))

    payload = json.loads(output)
    assert payload["url"] == "https://example.com/page"
    assert "Hello world" in payload["content"]
    assert "bad()" not in payload["content"]
    assert "[link: https://example.com/next]" in payload["content"]


@pytest.mark.asyncio
async def test_browser_fetch_rejects_a_redirect_to_a_private_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    tool = BrowserFetchTool(transport=httpx.MockTransport(handler))

    with pytest.raises(ValueError, match="blocked"):
        await tool.run(live_context(), ToolCall(input='{"url":"https://example.com/start"}'))


@pytest.mark.asyncio
async def test_resolution_rejects_a_private_literal_and_keeps_a_public_one() -> None:
    with pytest.raises(ValueError, match="blocked"):
        await resolve_public_address("127.0.0.1", 80)

    assert await resolve_public_address(PUBLIC_ADDRESS, 443) == PUBLIC_ADDRESS


@pytest.mark.asyncio
async def test_fetch_rejects_a_name_that_resolves_to_a_private_address() -> None:
    reached: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request)
        return httpx.Response(200, text="reached the private host")

    tool = BrowserFetchTool(transport=PinnedTransport(lookup=answers_with_private, inner=httpx.MockTransport(handler)))

    with pytest.raises(ValueError, match="blocked"):
        await tool.run(live_context(), ToolCall(input='{"url":"https://rebind.example/page"}'))

    # The refusal happens before the request is handed to anything that connects.
    assert reached == []


@pytest.mark.asyncio
async def test_fetch_rejects_a_name_whose_answers_are_not_all_public() -> None:
    async def mixed_answer(host: str, port: int) -> list[str]:
        return [PUBLIC_ADDRESS, PRIVATE_ADDRESS]

    tool = BrowserFetchTool(transport=PinnedTransport(lookup=mixed_answer, inner=httpx.MockTransport(answers_ok)))

    with pytest.raises(ValueError, match="blocked"):
        await tool.run(live_context(), ToolCall(input='{"url":"https://rebind.example/page"}'))


@pytest.mark.asyncio
async def test_fetch_dials_the_validated_address_under_the_original_name() -> None:
    reached: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        reached.append(request)
        return httpx.Response(200, text="<html><body><p>Hello world</p></body></html>")

    tool = BrowserFetchTool(transport=PinnedTransport(lookup=answers_with_public, inner=httpx.MockTransport(handler)))

    output = await tool.run(live_context(), ToolCall(input='{"url":"https://example.com/page"}'))

    assert reached[0].url.host == PUBLIC_ADDRESS
    assert reached[0].headers["host"] == "example.com"
    assert reached[0].extensions["sni_hostname"] == "example.com"
    payload = json.loads(output)
    assert payload["url"] == "https://example.com/page"
    assert "Hello world" in payload["content"]


@pytest.mark.asyncio
async def test_fetch_reports_a_host_it_cannot_resolve() -> None:
    async def unresolvable(host: str, port: int) -> list[str]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    async def empty(host: str, port: int) -> list[str]:
        return []

    for lookup in (unresolvable, empty):
        tool = BrowserFetchTool(transport=PinnedTransport(lookup=lookup, inner=httpx.MockTransport(answers_ok)))

        with pytest.raises(RuntimeError, match="could not resolve host"):
            await tool.run(live_context(), ToolCall(input='{"url":"https://absent.example/page"}'))


@pytest.mark.asyncio
async def test_browser_fetch_stops_at_the_redirect_cap() -> None:
    hops: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hops.append(request.url)
        return httpx.Response(302, headers={"location": "/again"})

    tool = BrowserFetchTool(transport=httpx.MockTransport(handler))

    with pytest.raises(RuntimeError, match="too many redirects"):
        await tool.run(live_context(), ToolCall(input='{"url":"https://example.com/start"}'))

    assert len(hops) == 6
