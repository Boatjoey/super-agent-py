"""A stdio MCP client and the tools a connected server contributes.

Ported from ``tools/mcp/client.go``. The Go client is built on the official
``go-sdk``; this port speaks the same JSON-RPC itself, because the repository
deliberately does not depend on an MCP SDK. The wire framing is Content-Length,
shared with the LSP client in this package.

The observable behaviour the Go implementation relies on is preserved: a connect
timeout, a per-call deadline, the ``initialize``/``initialized`` handshake,
``tools/list`` mapped to :class:`ToolSpec`, ``tools/call`` with bounded output,
an explicit environment, and discovered tools that are *always* risky.

Two ordering rules matter:

* The connect happens outside the manager lock. It can block for the whole
  connect timeout on a hung server, and holding the lock would freeze
  ``Tools``/``Servers`` for every reader. The closed and duplicate checks run
  again under the lock before the connection is installed.
* Truncation cuts on a rune boundary, so multibyte text does not turn into
  replacement characters at the cut point.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Final, cast

from super_agent.errors import JoinedError
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec

#: The MCP revision this client announces during the handshake.
protocol_version: Final[str] = "2025-06-18"
#: How long ``initialize`` and ``tools/list`` may take when a server sets none.
default_connect_timeout: Final[float] = 10.0
#: How long one ``tools/call`` may take when a server sets none.
default_call_timeout: Final[float] = 60.0
#: Bounds one tool result. The server controls the size, so the client must too.
max_result_bytes: Final[int] = 200_000
#: Bounds one frame, shared with the LSP client's reasoning.
max_message_bytes: Final[int] = 32 << 20

#: Basic process variables a server keeps even when its environment is explicit.
environment_keys: Final[tuple[str, ...]] = (
    "HOME",
    "LANG",
    "LC_ALL",
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "TMPDIR",
    "USER",
)


@dataclasses.dataclass(slots=True)
class ServerConfig:
    """One configured MCP stdio server."""

    Name: str = ""
    Command: str = ""
    Args: list[str] = dataclasses.field(default_factory=list[str])
    Env: dict[str, str] = dataclasses.field(default_factory=dict[str, str])
    CWD: str = ""
    ConnectTimeout: float = 0.0
    CallTimeout: float = 0.0


@dataclasses.dataclass(frozen=True, slots=True)
class ServerInfo:
    """One connected server, as reported by :meth:`Manager.Servers`."""

    Name: str = ""
    Tools: tuple[str, ...] = ()


def command_environment(overrides: Mapping[str, str]) -> dict[str, str]:
    """The environment a server process runs with.

    Only the basic process variables survive; everything else the agent holds is
    dropped unless the configuration names it explicitly.
    """
    environment: dict[str, str] = {}
    for key in environment_keys:
        value = os.environ.get(key)
        if value is not None:
            environment[key] = value
    for key in sorted(overrides):
        environment[key] = overrides[key]
    return environment


def tool_spec(tool: Mapping[str, Any]) -> ToolSpec:
    """Map one ``tools/list`` entry to the spec the model sees."""
    name = tool.get("name")
    if not isinstance(name, str) or name == "":
        raise RuntimeError("tool name is required")
    description = tool.get("description")
    parameters: dict[str, Any] = {"type": "object"}
    schema = tool.get("inputSchema")
    if schema is not None:
        if not isinstance(schema, Mapping):
            raise RuntimeError("tool input schema must be an object")
        # Go unmarshals into a map that already holds "type": "object", so the
        # default survives and the declared keys are merged over it.
        for key, value in cast("Mapping[str, Any]", schema).items():
            parameters[str(key)] = value
    return ToolSpec(
        Name=name,
        Description=description if isinstance(description, str) else "",
        Parameters=parameters,
        Risky=True,
    )


def format_result(result: Any) -> str:
    """Flatten one ``tools/call`` result into the text the model sees."""
    if not isinstance(result, Mapping):
        return ""
    payload = cast("Mapping[str, Any]", result)
    parts: list[str] = []
    content = payload.get("content")
    if isinstance(content, (list, tuple)):
        for item in cast("Sequence[Any]", content):
            if isinstance(item, Mapping) and cast("Mapping[str, Any]", item).get("type") == "text":
                parts.append(str(cast("Mapping[str, Any]", item).get("text", "")))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
    structured = payload.get("structuredContent")
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False))
    output = "\n".join(parts)
    if payload.get("isError") is True and output == "":
        output = "MCP tool reported an error"
    encoded = output.encode("utf-8")
    if len(encoded) > max_result_bytes:
        cut = max_result_bytes
        while cut > 0 and encoded[cut] & 0xC0 == 0x80:
            cut -= 1
        output = encoded[:cut].decode("utf-8") + "\n... truncated"
    return output


def _quote(value: str) -> str:
    """Go's ``%q`` for a string, so the error text reads the same."""
    return json.dumps(value)


async def read_header(reader: asyncio.StreamReader) -> int:
    """Read one frame's ``Content-Length``, enforcing the message cap."""
    length = 0
    while True:
        line = await reader.readline()
        if line == b"":
            raise RuntimeError("MCP server closed the connection")
        text = line.strip()
        if text == b"":
            break
        if text.lower().startswith(b"content-length:"):
            raw = text.split(b":", 1)[1].strip()
            try:
                length = int(raw)
            except ValueError as err:
                raise RuntimeError("invalid MCP content length") from err
    if length <= 0:
        raise RuntimeError("invalid MCP content length")
    if length > max_message_bytes:
        raise RuntimeError(f"MCP message of {length} bytes exceeds the {max_message_bytes} byte limit")
    return length


class _Session:
    """One JSON-RPC connection to a server process."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        stdin = process.stdin
        stdout = process.stdout
        if stdin is None or stdout is None:  # pragma: no cover - PIPE was requested
            raise RuntimeError("MCP server pipes are unavailable")
        self._process = process
        self._stdin = stdin
        self._pending: dict[int, asyncio.Future[Mapping[str, Any]]] = {}
        self._next_id = 0
        self._done = asyncio.Event()
        self._err: BaseException | None = None
        self._write_lock = asyncio.Lock()
        self._read_task = asyncio.create_task(self._read_loop(stdout))

    async def Initialize(self) -> None:
        """Complete the MCP handshake."""
        await self.request(
            "initialize",
            {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "super-agent", "version": "dev"},
            },
        )
        await self.notify("notifications/initialized", {})

    async def ListTools(self) -> list[Mapping[str, Any]]:
        """The tool descriptors the server advertises."""
        result = await self.request("tools/list", {})
        if not isinstance(result, Mapping):
            return []
        tools = cast("Mapping[str, Any]", result).get("tools")
        if not isinstance(tools, Sequence) or isinstance(tools, str):
            return []
        entries: list[Mapping[str, Any]] = []
        for tool in cast("Sequence[Any]", tools):
            if isinstance(tool, Mapping):
                entries.append(cast("Mapping[str, Any]", tool))
        return entries

    async def CallTool(self, name: str, arguments: Any) -> Any:
        """Invoke one remote tool."""
        return await self.request("tools/call", {"name": name, "arguments": arguments})

    async def request(self, method: str, params: Any) -> Any:
        """Send ``method`` and return its result, raising on an error reply."""
        if self._err is not None:
            raise self._err
        self._next_id += 1
        request_id = self._next_id
        future: asyncio.Future[Mapping[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            message = await self._await_reply(future)
        except BaseException:
            # Drop the registration without cancelling the future: the read loop
            # may still deliver into it, and resolving a cancelled future raises
            # inside that loop.
            self._pending.pop(request_id, None)
            raise
        error = message.get("error")
        if isinstance(error, Mapping):
            raise RuntimeError(f"MCP {method}: {cast('Mapping[str, Any]', error).get('message', '')}")
        return message.get("result")

    async def _await_reply(self, future: asyncio.Future[Mapping[str, Any]]) -> Mapping[str, Any]:
        """Wait for the reply, or for the server to stop."""
        done_task = asyncio.ensure_future(self._done.wait())
        waiters: set[asyncio.Future[Any]] = {future, done_task}
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            done_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await done_task
        if future.done():
            return future.result()
        raise self._err if self._err is not None else RuntimeError("MCP server stopped")

    async def notify(self, method: str, params: Any) -> None:
        """Send ``method`` without waiting for a reply."""
        await self.write({"jsonrpc": "2.0", "method": method, "params": params})

    async def write(self, message: Any) -> None:
        """Write one frame, preserving message order on the wire."""
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        frame = b"Content-Length: %d\r\n\r\n" % len(body) + body
        async with self._write_lock:
            if self._err is not None:
                raise self._err
            self._stdin.write(frame)
            await self._stdin.drain()

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        try:
            while True:
                length = await read_header(reader)
                body = await reader.readexactly(length)
                self._dispatch(body)
        except Exception as err:
            self._err = err
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
        if not isinstance(request_id, int):
            return
        future = self._pending.pop(request_id, None)
        if future is not None and not future.done():
            future.set_result(message)

    async def Close(self) -> None:
        """Shut the connection down and make sure the process is gone."""
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


class _Server:
    """One connected server and the tools it contributed."""

    def __init__(self, name: str, config: ServerConfig, session: _Session, call_timeout: float) -> None:
        self.name = name
        self.config = config
        self.session = session
        self.call_timeout = call_timeout
        self.tool_names: list[str] = []

    async def Close(self) -> None:
        """Close the underlying connection."""
        await self.session.Close()


class RemoteTool:
    """A tool discovered on a connected server."""

    def __init__(self, server: _Server, remote_name: str, spec: ToolSpec) -> None:
        self.server = server
        self.remote_name = remote_name
        self.spec = spec

    def Specs(self) -> list[ToolSpec]:
        return [self.spec]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        arguments: Any = {}
        if call.Input.strip() != "":
            try:
                arguments = json.loads(call.Input)
            except ValueError as err:
                raise RuntimeError(f"MCP tool {self.server.name}.{self.remote_name} input: {err}") from err
        try:
            result = await _with_deadline(
                ctx,
                self.server.session.CallTool(self.remote_name, arguments),
                self.server.call_timeout,
            )
        except Exception as err:
            raise RuntimeError(f"call MCP tool {self.server.name}.{self.remote_name}: {err}") from err
        return format_result(result)


async def _with_deadline[T](ctx: RunContext, work: Awaitable[T], timeout: float) -> T:
    """Await ``work``, failing when ``timeout`` elapses or ``ctx`` is cancelled."""
    task = asyncio.ensure_future(work)
    cancel_task = asyncio.ensure_future(ctx.Done().wait())
    try:
        await asyncio.wait({task, cancel_task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            return task.result()
        ctx.RaiseIfCancelled()
        raise TimeoutError(f"MCP request timed out after {timeout} seconds")
    finally:
        for pending in (task, cancel_task):
            if pending.done():
                continue
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending


async def connect_server(ctx: RunContext, config: ServerConfig) -> tuple[_Server, list[RemoteTool]]:
    """Start one server, complete the handshake, and discover its tools."""
    if config.Name == "" or config.Command == "":
        raise RuntimeError("MCP server name and command are required")
    connect_timeout = config.ConnectTimeout if config.ConnectTimeout > 0 else default_connect_timeout
    call_timeout = config.CallTimeout if config.CallTimeout > 0 else default_call_timeout
    process = await asyncio.create_subprocess_exec(
        config.Command,
        *config.Args,
        cwd=config.CWD or None,
        env=command_environment(config.Env),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    session = _Session(process)
    try:
        try:
            await _with_deadline(ctx, session.Initialize(), connect_timeout)
        except Exception as err:
            raise RuntimeError(f"connect MCP server {_quote(config.Name)}: {err}") from err
        try:
            listed = await _with_deadline(ctx, session.ListTools(), connect_timeout)
        except Exception as err:
            raise RuntimeError(f"list tools from MCP server {_quote(config.Name)}: {err}") from err
        connected = _Server(name=config.Name, config=config, session=session, call_timeout=call_timeout)
        batch: list[RemoteTool] = []
        for tool in listed:
            try:
                spec = tool_spec(tool)
            except Exception as err:
                raise RuntimeError(f"map MCP tool from server {_quote(config.Name)}: {err}") from err
            batch.append(RemoteTool(server=connected, remote_name=spec.Name, spec=spec))
            connected.tool_names.append(spec.Name)
        return connected, batch
    except BaseException:
        with contextlib.suppress(Exception):
            await session.Close()
        raise


#: The hook :meth:`Manager.Restart` runs to move a server's tools in the registry.
type ReplaceTools = Callable[[list[str], list[RemoteTool]], None]


class Manager:
    """The MCP servers one session has connected."""

    def __init__(self) -> None:
        self._servers: dict[str, _Server] = {}
        self._tools: list[RemoteTool] = []
        self._closed = False
        # Only Add and Restart suspend mid-operation; everything else mutates the
        # maps without yielding, so it is atomic by construction.
        self._lock = asyncio.Lock()

    async def Add(self, ctx: RunContext, config: ServerConfig) -> list[RemoteTool]:
        """Connect a server and install the tools it contributes."""
        async with self._lock:
            if self._closed:
                raise RuntimeError("MCP manager is closed")
            if config.Name in self._servers:
                raise RuntimeError(f"MCP server {_quote(config.Name)} is duplicated")
        connected, batch = await connect_server(ctx, config)
        async with self._lock:
            if self._closed:
                await connected.Close()
                raise RuntimeError("MCP manager is closed")
            if config.Name in self._servers:
                await connected.Close()
                raise RuntimeError(f"MCP server {_quote(config.Name)} is duplicated")
            existing = {tool.Specs()[0].Name for tool in self._tools}
            for tool in batch:
                name = tool.Specs()[0].Name
                if name in existing:
                    await connected.Close()
                    raise RuntimeError(f"MCP tool {_quote(name)} is duplicated")
            self._servers[config.Name] = connected
            self._tools.extend(batch)
        return list(batch)

    async def Remove(self, name: str) -> list[str]:
        """Disconnect a server and forget its tools."""
        async with self._lock:
            connected = self._servers.pop(name, None)
            if connected is None:
                raise RuntimeError(f"MCP server {_quote(name)} not found")
            self._tools = [tool for tool in self._tools if tool.server is not connected]
            names = list(connected.tool_names)
        await connected.Close()
        return names

    async def Restart(self, ctx: RunContext, name: str, replace: ReplaceTools | None = None) -> None:
        """Reconnect a server, optionally rewriting its tools in the registry."""
        async with self._lock:
            old = self._servers.get(name)
            if old is None:
                raise RuntimeError(f"MCP server {_quote(name)} not found")
            config = old.config
        replacement, batch = await connect_server(ctx, config)
        try:
            async with self._lock:
                current = self._servers.get(name)
                if current is None or current is not old:
                    raise RuntimeError(f"MCP server {_quote(name)} changed while restarting")
                if replace is not None:
                    replace(list(old.tool_names), list(batch))
                kept = [tool for tool in self._tools if tool.server is not old]
                self._tools = [*kept, *batch]
                self._servers[name] = replacement
        except BaseException:
            with contextlib.suppress(Exception):
                await replacement.Close()
            raise
        await old.Close()

    def Servers(self) -> list[ServerInfo]:
        """Every connected server, ordered by name."""
        infos = [ServerInfo(Name=name, Tools=tuple(server.tool_names)) for name, server in self._servers.items()]
        infos.sort(key=lambda info: info.Name)
        return infos

    def Tools(self) -> list[RemoteTool]:
        """Every tool discovered so far."""
        return list(self._tools)

    async def Close(self) -> None:
        """Close every server, joining the failures the way ``errors.Join`` does."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            names = sorted(self._servers)
            servers = [self._servers[name] for name in names]
        errors: list[BaseException] = []
        for name, server in zip(names, servers, strict=True):
            try:
                await server.Close()
            except Exception as err:
                errors.append(RuntimeError(f"close MCP server {_quote(name)}: {err}"))
        if errors:
            raise JoinedError(*errors)


async def Connect(ctx: RunContext, configs: Sequence[ServerConfig]) -> Manager:
    """Connect every configured server, or close what was connected already."""
    manager = Manager()
    for config in configs:
        try:
            await manager.Add(ctx, config)
        except BaseException:
            with contextlib.suppress(Exception):
                await manager.Close()
            raise
    return manager
