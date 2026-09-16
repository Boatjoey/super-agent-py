"""A fake MCP server exposing one ``echo`` tool.

Run as ``python -m tests.helpers.mcp_echo_server [--call-delay SECONDS]``. It
stands in for a server built with the official SDK: it completes the
``initialize``/``initialized`` handshake, advertises a single ``echo`` tool whose
schema requires ``text``, and answers ``tools/call`` with ``echo: <text>``.

Frames are Content-Length delimited, the framing the hand-written client in
``super_agent/tools/mcp`` speaks. ``--call-delay`` postpones a call's reply so a
test can exercise the call deadline.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from typing import Any, BinaryIO, cast

CONTENT_LENGTH = b"content-length:"


def _field(value: Any, key: str) -> Any:
    """``value[key]`` when ``value`` is a mapping, and ``None`` otherwise."""
    if isinstance(value, Mapping):
        return cast("Mapping[str, Any]", value).get(key)
    return None


PROTOCOL_VERSION = "2025-06-18"

TOOL: dict[str, Any] = {
    "name": "echo",
    "description": "Echo text",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}


def _read_message(stream: BinaryIO) -> bytes | None:
    """Read one Content-Length frame, or ``None`` at end of input."""
    length = 0
    while True:
        line = stream.readline()
        if line == b"":
            return None
        text = line.strip()
        if text == b"":
            break
        if text.lower().startswith(CONTENT_LENGTH):
            try:
                length = int(text.split(b":", 1)[1].strip())
            except ValueError:
                return None
    if length <= 0:
        return None
    body = stream.read(length)
    if len(body) < length:
        return None
    return body


def _write_message(stream: BinaryIO, message: Any) -> None:
    """Write one Content-Length frame and flush it."""
    body = json.dumps(message).encode("utf-8")
    stream.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    stream.flush()


def _numeric_option(name: str, default: float) -> float:
    if name in sys.argv[1:]:
        return float(sys.argv[sys.argv.index(name) + 1])
    return default


def _call_result(params: Any) -> dict[str, Any]:
    """Answer one ``tools/call``."""
    text = _field(_field(params, "arguments"), "text")
    return {"content": [{"type": "text", "text": "echo: " + (text if isinstance(text, str) else "")}]}


def main() -> int:
    """Serve frames until end of input."""
    call_delay = _numeric_option("--call-delay", 0.0)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        body = _read_message(stdin)
        if body is None:
            return 0
        try:
            request: Any = json.loads(body)
        except ValueError:
            continue
        if not isinstance(request, Mapping):
            continue
        method = _field(request, "method")
        request_id = _field(request, "id")
        if request_id is None:
            # A notification, including notifications/initialized.
            continue
        if method == "initialize":
            result: Any = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [TOOL]}
        elif method == "tools/call":
            if call_delay > 0:
                time.sleep(call_delay)
            result = _call_result(_field(request, "params"))
        else:
            _write_message(
                stdout,
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "unknown method"}},
            )
            continue
        _write_message(stdout, {"jsonrpc": "2.0", "id": request_id, "result": result})


if __name__ == "__main__":
    raise SystemExit(main())
