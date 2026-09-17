"""Network tools: a web search and a page fetch.

Both are risky tools under the common permission flow, and the fetch accepts only
public HTTP(S) targets: it resolves each host itself and connects to an address
that resolution vouched for, caps redirects, response size, and total time, and
extracts page text without executing scripts.

The ``transport`` field on each tool exists so tests can supply an
:class:`httpx.MockTransport`; production registries construct them empty.
"""

from __future__ import annotations

import asyncio
import dataclasses
import ipaddress
import json
import socket
import urllib.parse
from collections.abc import Awaitable, Callable
from typing import Any, Final

import httpx
import lxml.html

from super_agent.jsonutil import json_field
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools.files import decode_args, object_schema

max_browser_bytes: Final[int] = 2 << 20
_user_agent: Final[str] = "Super-Agent/0.1"
_max_redirects: Final[int] = 5
_total_timeout: Final[float] = 20.0
_text_limit: Final[int] = 100_000

_blocked: Final[str] = "browser blocked a private or local address"
_not_public: Final[str] = "browser URL must be public HTTP(S)"
_unresolved: Final[str] = "browser could not resolve host"

#: A DNS lookup: every address a host answers with, in the resolver's own order.
Lookup = Callable[[str, int], Awaitable[list[str]]]

#: lxml ships no type information here, so the DOM walk goes through ``Any``.
_html: Any = lxml.html


@dataclasses.dataclass(frozen=True, slots=True)
class _WebSearchArgs:
    query: str = dataclasses.field(default="", metadata=json_field(name="query"))


@dataclasses.dataclass(frozen=True, slots=True)
class _BrowserFetchArgs:
    url: str = dataclasses.field(default="", metadata=json_field(name="url"))


@dataclasses.dataclass(frozen=True, slots=True)
class WebSearchTool:
    """Search the public web."""

    transport: httpx.AsyncBaseTransport | None = None

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="web_search",
                description="Search the public web.",
                risky=True,
                parameters=object_schema({"query": {"type": "string"}}, ["query"]),
            )
        ]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        args = decode_args(call.input, _WebSearchArgs)
        query = args.query.strip()
        if query == "":
            raise RuntimeError("search query is required")
        endpoint = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        content, final_url = await fetch_public(ctx, endpoint, self.transport)
        return extracted_page(final_url, content)


@dataclasses.dataclass(frozen=True, slots=True)
class BrowserFetchTool:
    """Fetch and extract text from a public HTTP(S) page."""

    transport: httpx.AsyncBaseTransport | None = None

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="browser_fetch",
                description="Fetch and extract text from a public HTTP(S) page.",
                risky=True,
                parameters=object_schema({"url": {"type": "string"}}, ["url"]),
            )
        ]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        args = decode_args(call.input, _BrowserFetchArgs)
        content, final_url = await fetch_public(ctx, args.url.strip(), self.transport)
        return extracted_page(final_url, content)


async def fetch_public(
    ctx: RunContext,
    raw_url: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str]:
    """Fetch ``raw_url``, or refuse before any connection is attempted."""
    url = validate_public_url(raw_url)
    async with asyncio.timeout(_total_timeout):
        async with httpx.AsyncClient(
            transport=PinnedTransport() if transport is None else transport,
            timeout=httpx.Timeout(_total_timeout),
            follow_redirects=False,
            headers={"User-Agent": _user_agent},
        ) as client:
            for hop in range(_max_redirects + 1):
                ctx.raise_if_cancelled()
                async with client.stream("GET", url) as response:
                    if response.is_redirect and "location" in response.headers:
                        if hop >= _max_redirects:
                            raise RuntimeError("too many redirects")
                        # Every hop is revalidated, so a public URL cannot
                        # redirect the fetch at a private one.
                        url = validate_public_url(urllib.parse.urljoin(str(response.url), response.headers["location"]))
                        continue
                    if not 200 <= response.status_code < 300:
                        raise RuntimeError(f"HTTP status {response.status_code} {response.reason_phrase}")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_browser_bytes:
                            raise RuntimeError("browser response exceeds 2 MiB")
                    return bytes(content), str(response.url)
    raise RuntimeError("too many redirects")  # pragma: no cover - the loop returns or raises


async def lookup_host(host: str, port: int) -> list[str]:
    """Every address ``host`` answers with, in the resolver's own order."""
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(answer[4][0]) for answer in answers]


async def resolve_public_address(
    host: str,
    port: int,
    lookup: Lookup = lookup_host,
) -> str:
    """The one address to dial for ``host``, or a refusal.

    Every answer must be public. A name that answers with a public address and a
    private one is refused rather than half-used, because the private answer is
    the one a rebinding attempt is after.
    """
    try:
        addresses = await lookup(host, port)
    except OSError:
        raise RuntimeError(_unresolved) from None
    if not addresses:
        raise RuntimeError(_unresolved)
    if not all(public_ip(address) for address in addresses):
        raise ValueError(_blocked)
    return addresses[0]


def _port(url: httpx.URL) -> int:
    """The port to dial: a URL omits it when the scheme already fixes it."""
    if url.port is not None:
        return url.port
    return 443 if url.scheme == "https" else 80


class PinnedTransport(httpx.AsyncBaseTransport):
    """A transport that dials only an address resolution vouched for.

    The name is resolved here and the request is sent to the address that was
    checked, so nothing resolves a second time between the check and the
    connection. The name still travels as the ``Host`` header and as the TLS
    server name, so a certificate is verified against the name that was asked
    for: no idle connection is reused, because the pool keys one by the address
    it dialled and its TLS session belongs to a single name.

    ``lookup`` and ``inner`` are the seams a test substitutes; a production
    registry takes the defaults.
    """

    def __init__(
        self,
        lookup: Lookup = lookup_host,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._lookup = lookup
        self._inner: httpx.AsyncBaseTransport = (
            httpx.AsyncHTTPTransport(limits=httpx.Limits(max_keepalive_connections=0)) if inner is None else inner
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        address = await resolve_public_address(url.host, _port(url), self._lookup)
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=address),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        return await self._inner.handle_async_request(pinned)

    async def aclose(self) -> None:
        await self._inner.aclose()


def validate_public_url(raw_url: str) -> str:
    """Return ``raw_url`` when it names a public HTTP(S) target."""
    try:
        parsed = urllib.parse.urlsplit(raw_url)
        host = (parsed.hostname or "").lower()
    except ValueError:
        raise ValueError(_not_public) from None
    if host == "" or parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(_not_public)
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError(_blocked)
    if _is_ip_literal(host) and not public_ip(host):
        raise ValueError(_blocked)
    return raw_url


def public_ip(address: str) -> bool:
    """Whether ``address`` is a globally routable unicast address."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    # ``is_global`` rejects private, loopback, and link-local ranges, which is
    # the statement that matters here.
    return parsed.is_global


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def extracted_page(raw_url: str, content: bytes) -> str:
    """Readable page text plus resolved links, as a JSON object."""
    try:
        root: Any = _html.fromstring(content)
    except Exception:
        # A parse error becomes plain text rather than a failed fetch.
        return _page_json(raw_url, content.decode("utf-8", "replace"))
    lines: list[str] = []
    _walk(root, raw_url, lines)
    return _page_json(raw_url, "\n".join(lines))


def _page_json(raw_url: str, text: str) -> str:
    return json.dumps(
        {"url": raw_url, "content": limit_text(text, _text_limit)},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _walk(element: Any, raw_url: str, lines: list[str]) -> None:
    """Append text nodes and resolved links in document order."""
    tag = element.tag
    if not isinstance(tag, str):
        # Comments and processing instructions carry no element text.
        _walk_children(element, raw_url, lines)
        return
    name = tag.lower()
    if name in ("script", "style", "noscript"):
        return
    if name == "a":
        href = element.get("href")
        if href:
            lines.append("[link: " + urllib.parse.urljoin(raw_url, href) + "]")
    if element.text:
        text = " ".join(element.text.split())
        if text:
            lines.append(text)
    _walk_children(element, raw_url, lines)


def _walk_children(element: Any, raw_url: str, lines: list[str]) -> None:
    for child in element:
        _walk(child, raw_url, lines)
        if child.tail:
            text = " ".join(child.tail.split())
            if text:
                lines.append(text)


def limit_text(value: str, limit: int) -> str:
    """Cap ``value`` at ``limit`` characters, marking the cut."""
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[truncated]"
