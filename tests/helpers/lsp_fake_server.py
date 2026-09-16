"""A fake language server the LSP tests spawn.

Run as ``python -m tests.helpers.lsp_fake_server [--delay SECONDS]
[--diagnostics-delay SECONDS]``. It mirrors the helper embedded in
``tests/tools/lsp_test.go``: it answers ``initialize`` and ``shutdown`` with an
empty capability set, answers every other request with a one-element result, and
pushes a diagnostic as soon as a document is opened.

``--delay`` postpones every request except the handshake, so a test can cancel a
request while its reply is still in flight. ``--diagnostics-delay`` postpones
the published diagnostic, so a test can prove the client waits for it.
"""

from __future__ import annotations

import json
import os
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


def _diagnostic(uri: str) -> dict[str, Any]:
    """The diagnostic pushed for ``uri``.

    ``source`` carries the server's working directory so a test can observe the
    client reconnecting after the workspace moves.
    """
    return {"uri": uri, "diagnostics": [{"message": "fake diagnostic", "source": os.getcwd()}]}


def main() -> int:
    """Serve frames until ``exit`` or end of input."""
    delay = _numeric_option("--delay", 0.0)
    diagnostics_delay = _numeric_option("--diagnostics-delay", 0.0)
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
        if method == "exit":
            return 0
        if method == "textDocument/didOpen":
            uri = _field(_field(request, "params"), "textDocument")
            uri = _field(uri, "uri")
            if diagnostics_delay > 0:
                time.sleep(diagnostics_delay)
            _write_message(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "method": "textDocument/publishDiagnostics",
                    "params": _diagnostic(uri if isinstance(uri, str) else ""),
                },
            )
            continue
        request_id = _field(request, "id")
        if request_id is None:
            continue
        if delay > 0 and method != "initialize":
            time.sleep(delay)
        result: Any = [{"name": "Main"}]
        if method in ("initialize", "shutdown"):
            result = {"capabilities": {}}
        _write_message(stdout, {"jsonrpc": "2.0", "id": request_id, "result": result})


if __name__ == "__main__":
    raise SystemExit(main())
