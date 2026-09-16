"""A stdio language-server client and the tools a manager exposes.

A language server is a long-lived subprocess speaking JSON-RPC over Content-Length
frames on its stdin and stdout; its stderr is discarded rather than forwarded,
because the TUI owns the terminal and a chatty server (gopls, rust-analyzer) would
garble it.

The pending map, the diagnostics map, and the stored error need no lock: the event
loop cannot interleave a synchronous mutation. Wire writes are serialised by a
lock, because a write really does suspend the task part-way through a frame.

Two deliberate behaviours are worth naming:

* The read loop is one task. On any failure it records the error and then, in a
  single ``finally``, signals ``done``; requests wait on that signal so a dead
  server fails every waiter instead of hanging them.
* A request that loses its context drops its pending registration but does *not*
  cancel the future. The read loop may still deliver a reply into it, and
  resolving a cancelled future raises inside that loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any, Final, cast

from super_agent.errors import Cancelled, JoinedError
from super_agent.jsonutil import json_field
from super_agent.runtime.protocol.run_context import DEFAULT_CANCEL_REASON, RunContext, live_context
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools.files import decode_args
from super_agent.tools.workspace import WorkspaceContext

#: The largest frame the client will accept. The length is server-controlled, so
#: a buggy or hostile server must not be able to make the agent allocate without
#: bound.
max_message_bytes: Final[int] = 32 << 20
#: Bounds one wire write. A server that stops reading its stdin would otherwise
#: block the writer forever while holding the write lock, wedging every future
#: request before its context could fire.
write_timeout_seconds: Final[float] = 30.0
#: How long a pushed diagnostic is given to arrive after a ``didOpen``.
diagnostics_settle_seconds: Final[float] = 0.3
#: Bounds the graceful shutdown attempted by :meth:`_Client.close`.
shutdown_timeout_seconds: Final[float] = 1.0

#: The five tool names every configured manager exposes, in order.
tool_names: Final[tuple[str, ...]] = (
    "lsp_diagnostics",
    "lsp_symbols",
    "lsp_definition",
    "lsp_references",
    "lsp_outline",
)


@dataclasses.dataclass(frozen=True, slots=True)
class ServerConfig:
    """One configured language server.

    ``Root`` is filled in by :func:`connect` from the workspace's working
    directory, so callers leave it empty.
    """

    name: str = ""
    command: str = ""
    language_id: str = ""
    root: str = ""
    args: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _RpcError:
    """A JSON-RPC error object."""

    code: int = 0
    message: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class _Response:
    """One JSON-RPC reply."""

    result: Any = None
    error: _RpcError | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _ToolInput:
    """The arguments every LSP tool accepts."""

    path: str = dataclasses.field(default="", metadata=json_field(name="path"))
    query: str = dataclasses.field(default="", metadata=json_field(name="query"))
    line: int = dataclasses.field(default=0, metadata=json_field(name="line"))
    column: int = dataclasses.field(default=0, metadata=json_field(name="column"))


async def read_header(reader: asyncio.StreamReader) -> int:
    """Read one frame's ``Content-Length``, enforcing the message cap."""
    length = 0
    while True:
        line = await reader.readline()
        if line == b"":
            raise RuntimeError("LSP server closed the connection")
        text = line.strip()
        if text == b"":
            break
        if text.lower().startswith(b"content-length:"):
            raw = text.split(b":", 1)[1].strip()
            try:
                length = int(raw)
            except ValueError as err:
                raise RuntimeError("invalid LSP content length") from err
    if length <= 0:
        raise RuntimeError("invalid LSP content length")
    if length > max_message_bytes:
        raise RuntimeError(f"LSP message of {length} bytes exceeds the {max_message_bytes} byte limit")
    return length


def file_uri(path: str) -> str:
    """The ``file://`` URI for ``path``."""
    return "file://" + urllib.parse.quote(path.replace(os.sep, "/"), safe="/")


def pretty(value: Any) -> str:
    """``value`` as indented JSON, or ``"null"`` when there is nothing."""
    if value is None:
        return "null"
    return json.dumps(value, indent=2, ensure_ascii=False)


class _Client:
    """One live connection to a language server."""

    def __init__(self, config: ServerConfig, process: asyncio.subprocess.Process) -> None:
        stdin = process.stdin
        stdout = process.stdout
        if stdin is None or stdout is None:  # pragma: no cover - PIPE was requested
            raise RuntimeError("LSP server pipes are unavailable")
        self.config = config
        self._process = process
        self._stdin = stdin
        self._pending: dict[int, asyncio.Future[_Response]] = {}
        self._diagnostics: dict[str, Any] = {}
        self._next_id = 0
        self._done = asyncio.Event()
        self._err: BaseException | None = None
        self._write_lock = asyncio.Lock()
        self._read_task = asyncio.create_task(self._read_loop(stdout))

    def diagnostics_for(self, uri: str) -> Any:
        """The last diagnostics pushed for ``uri``, or ``None``."""
        return self._diagnostics.get(uri)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                length = await read_header(reader)
                body = await reader.readexactly(length)
                self._dispatch(body)
        except Exception as err:
            self.fail(err)
        finally:
            self._done.set()

    def _dispatch(self, body: bytes) -> None:
        try:
            envelope = json.loads(body)
        except ValueError:
            return
        if not isinstance(envelope, Mapping):
            return
        message = cast("Mapping[str, Any]", envelope)
        request_id = message.get("id")
        if request_id is not None:
            future = self._pending.pop(request_id, None) if isinstance(request_id, int) else None
            if future is not None and not future.done():
                future.set_result(_Response(result=message.get("result"), error=_parse_error(message.get("error"))))
            return
        if message.get("method") == "textDocument/publishDiagnostics":
            params = message.get("params")
            if isinstance(params, Mapping):
                document = cast("Mapping[str, Any]", params)
                uri = document.get("uri")
                if isinstance(uri, str):
                    self._diagnostics[uri] = document.get("diagnostics")

    def fail(self, err: BaseException) -> None:
        """Record the error every waiter should observe."""
        self._err = err

    async def request(self, ctx: RunContext, method: str, params: Any) -> Any:
        """Send ``method`` and return its result, raising on an error reply."""
        if self._err is not None:
            raise self._err
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[_Response] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        except BaseException:
            self._pending.pop(request_id, None)
            raise
        try:
            reply = await self._await_reply(future, ctx)
        except BaseException:
            # Drop the registration without cancelling the future: the read loop
            # may still deliver into it, and resolving a cancelled future raises
            # inside that loop.
            self._pending.pop(request_id, None)
            raise
        if reply.error is not None:
            raise RuntimeError(f"LSP {method}: {reply.error.message}")
        return reply.result

    async def _await_reply(self, future: asyncio.Future[_Response], ctx: RunContext) -> _Response:
        """Wait for the reply, the server stopping, or the run being cancelled."""
        done_task = asyncio.ensure_future(self._done.wait())
        cancel_task = asyncio.ensure_future(ctx.done().wait())
        waiters: set[asyncio.Future[Any]] = {future, done_task, cancel_task}
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in (done_task, cancel_task):
                waiter.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiter
        if future.done():
            return future.result()
        if self._done.is_set():
            raise self._err if self._err is not None else RuntimeError("LSP server stopped")
        ctx.raise_if_cancelled()
        raise Cancelled(DEFAULT_CANCEL_REASON)

    async def notify(self, method: str, params: Any) -> None:
        """Send ``method`` without waiting for a reply."""
        await self.write({"jsonrpc": "2.0", "method": method, "params": params})

    async def write(self, message: Any) -> None:
        """Write one frame, poisoning the client when the write times out."""
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        frame = b"Content-Length: %d\r\n\r\n" % len(body) + body
        async with self._write_lock:
            if self._err is not None:
                raise self._err
            try:
                async with asyncio.timeout(write_timeout_seconds):
                    self._stdin.write(frame)
                    await self._stdin.drain()
            except TimeoutError:
                # The pipe is wedged; this client is unrecoverable. Poison it so
                # later requests fail fast instead of piling onto the dead pipe.
                error = RuntimeError("LSP server write timed out")
                self.fail(error)
                raise error from None

    async def close(self) -> None:
        """Ask the server to shut down, then make sure the process is gone."""
        with contextlib.suppress(Exception):
            async with asyncio.timeout(shutdown_timeout_seconds):
                await self.request(live_context(), "shutdown", None)
        with contextlib.suppress(Exception):
            await self.notify("exit", None)
        self._stdin.close()
        if self._process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                self._process.kill()
        with contextlib.suppress(Exception):
            await self._process.wait()
        if not self._read_task.done():
            self._read_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._read_task
        with contextlib.suppress(Exception):
            await self._stdin.wait_closed()


def _parse_error(value: Any) -> _RpcError | None:
    """Build the error half of a reply, or ``None`` when there is none."""
    if not isinstance(value, Mapping):
        return None
    error = cast("Mapping[str, Any]", value)
    code = error.get("code")
    message = error.get("message")
    return _RpcError(code=code if isinstance(code, int) else 0, message=message if isinstance(message, str) else "")


async def _start(ctx: RunContext, config: ServerConfig) -> _Client:
    """Spawn one server and complete the LSP handshake."""
    if config.name.strip() == "" or config.command.strip() == "":
        raise RuntimeError("name and command are required")
    process = await asyncio.create_subprocess_exec(
        config.command,
        *config.args,
        cwd=config.root or None,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        # Server stderr is dropped, not forwarded: the TUI owns the terminal.
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    client = _Client(config, process)
    try:
        await client.request(
            ctx,
            "initialize",
            {"processId": os.getpid(), "rootUri": file_uri(config.root), "capabilities": {}},
        )
        await client.notify("initialized", {})
    except BaseException:
        await client.close()
        raise
    return client


async def _wait_or_cancel(ctx: RunContext, delay: float) -> None:
    """Sleep ``delay`` seconds, or until ``ctx`` is cancelled."""
    done = asyncio.ensure_future(ctx.done().wait())
    try:
        await asyncio.wait([done], timeout=delay)
    finally:
        done.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await done
    ctx.raise_if_cancelled()


class Manager:
    """Every language server one session has connected."""

    def __init__(self, workspace: WorkspaceContext, configs: Sequence[ServerConfig]) -> None:
        self._workspace = workspace
        self.root = ""
        self.configs: list[ServerConfig] = list(configs)
        self.clients: list[_Client] = []

    async def connect_clients(self, ctx: RunContext, configs: Sequence[ServerConfig], root: str) -> list[_Client]:
        """Start every configured server rooted at ``root``."""
        clients: list[_Client] = []
        for config in configs:
            try:
                connected = await _start(ctx, dataclasses.replace(config, root=root))
            except Exception as err:
                for client in clients:
                    await client.close()
                raise RuntimeError(f"connect LSP {config.name}: {err}") from err
            clients.append(connected)
        return clients

    def tools(self) -> list[Tool]:
        """The five LSP tools, or none when nothing is configured."""
        if not self.configs:
            return []
        return [Tool(name=name, manager=self) for name in tool_names]

    async def ensure_workspace(self, ctx: RunContext) -> None:
        """Reconnect every server after the workspace working directory moves."""
        desired = self._workspace.get_cwd()
        if desired == self.root:
            return
        clients = await self.connect_clients(ctx, self.configs, desired)
        old = self.clients
        self.clients = clients
        self.root = desired
        for client in old:
            await client.close()

    def clients_snapshot(self) -> list[_Client]:
        return list(self.clients)

    def client_for(self, clients: Sequence[_Client], path: str) -> tuple[_Client, str]:
        """The server covering ``path`` and its canonical form."""
        absolute = self._workspace.resolve_path(path)
        # ResolvePath canonicalizes symlinks, so the containment check runs on
        # the real target rather than a lexical path a symlink could point out.
        if not self._workspace.can_read(absolute):
            raise RuntimeError("path is outside readable workspace roots")
        extension = os.path.splitext(absolute)[1].removeprefix(".")
        for client in clients:
            for supported in client.config.extensions:
                if supported.removeprefix(".") == extension:
                    return client, absolute
        raise RuntimeError("no LSP server configured for ." + extension)

    async def close(self) -> None:
        """Close every connected server."""
        clients = self.clients
        self.clients = []
        errors: list[BaseException] = []
        for client in clients:
            try:
                await client.close()
            except Exception as err:  # pragma: no cover - Close swallows its own failures
                errors.append(err)
        if errors:
            raise JoinedError(*errors)


async def connect(ctx: RunContext, workspace: WorkspaceContext, configs: Sequence[ServerConfig]) -> Manager:
    """Connect every configured server for ``workspace``."""
    manager = Manager(workspace, configs)
    clients = await manager.connect_clients(ctx, manager.configs, workspace.get_cwd())
    manager.root = workspace.get_cwd()
    manager.clients = clients
    return manager


class Tool:
    """One of the five fixed LSP tools, bound to a manager."""

    def __init__(self, name: str, manager: Manager) -> None:
        self.name = name
        self.manager = manager

    def specs(self) -> list[ToolSpec]:
        properties: dict[str, Any] = {
            "path": {"type": "string"},
            "line": {"type": "integer"},
            "column": {"type": "integer"},
            "query": {"type": "string"},
        }
        required = ["query"] if self.name == "lsp_symbols" else ["path"]
        return [
            ToolSpec(
                name=self.name,
                description="Query the configured language server.",
                parameters={"type": "object", "properties": properties, "required": required},
            )
        ]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        args = decode_args(call.input, _ToolInput)
        await self.manager.ensure_workspace(ctx)
        clients = self.manager.clients_snapshot()
        if self.name == "lsp_symbols":
            if not clients:
                raise RuntimeError("no LSP servers configured")
            result = await clients[0].request(ctx, "workspace/symbol", {"query": args.query})
            return pretty(result)
        client, path = self.manager.client_for(clients, args.path)
        content = _read_text(path)
        uri = file_uri(path)
        await client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": client.config.language_id,
                    "version": 1,
                    "text": content,
                }
            },
        )
        text_document = {"uri": uri}
        position = {"line": max(0, args.line - 1), "character": max(0, args.column - 1)}
        if self.name == "lsp_diagnostics":
            await _wait_or_cancel(ctx, diagnostics_settle_seconds)
            stored = client.diagnostics_for(uri)
            return pretty([] if stored is None else stored)
        result: Any
        if self.name == "lsp_outline":
            result = await client.request(ctx, "textDocument/documentSymbol", {"textDocument": text_document})
        elif self.name == "lsp_definition":
            result = await client.request(
                ctx,
                "textDocument/definition",
                {"textDocument": text_document, "position": position},
            )
        elif self.name == "lsp_references":
            result = await client.request(
                ctx,
                "textDocument/references",
                {"textDocument": text_document, "position": position, "context": {"includeDeclaration": True}},
            )
        else:
            raise RuntimeError("unknown LSP tool")
        return pretty(result)


def _read_text(path: str) -> str:
    """Read ``path`` as text, replacing undecodable bytes."""
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", errors="replace")
