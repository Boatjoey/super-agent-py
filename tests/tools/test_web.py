"""Port of ``tests/tools/web_test.go``, plus the offline fetch cases.

No test here reaches the network: the fetch cases drive the tool through an
:class:`httpx.MockTransport`, and the rejection cases never get as far as a
connection.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import ToolCall
from super_agent.tools import BrowserFetchTool, DefaultRegistry
from tests.tools.test_workspace import workspace_for


@pytest.mark.asyncio
async def test_browser_rejects_private_and_non_http_addresses() -> None:
    tool = BrowserFetchTool()
    for raw in (
        '{"url":"http://127.0.0.1/private"}',
        '{"url":"http://localhost/private"}',
        '{"url":"file:///etc/passwd"}',
    ):
        with pytest.raises((ValueError, RuntimeError)) as caught:
            await tool.Run(LiveContext(), ToolCall(Input=raw))
        message = str(caught.value)
        assert "blocked" in message or "HTTP(S)" in message


def test_web_tools_are_risky_network_tools(tmp_path: Path) -> None:
    seen = {
        spec.Name: spec.Risky
        for spec in DefaultRegistry(workspace_for(tmp_path)).Specs()
        if spec.Name in ("web_search", "browser_fetch")
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

    output = await tool.Run(LiveContext(), ToolCall(Input='{"url":"https://example.com/page"}'))

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
        await tool.Run(LiveContext(), ToolCall(Input='{"url":"https://example.com/start"}'))
