"""Port of ``tests/tools/mcp_test.go``, plus the environment and bound cases.

The Go test spawns its own test binary and builds the server with the official
SDK. Here the server is :mod:`tests.helpers.mcp_echo_server`, run as
``python -m``, and it speaks the same hand-written framing the client does. No
test touches the network; the environment is explicit, which is why the helper
needs ``PYTHONPATH`` passed as an override.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import ToolCall
from super_agent.tools import NewRegistry
from super_agent.tools.mcp import Connect, Manager, RemoteTool, ServerConfig
from super_agent.tools.mcp.client import command_environment, format_result, max_result_bytes, tool_spec

REPO_ROOT = Path(__file__).resolve().parents[2]


def fake_config(name: str, *extra: str) -> ServerConfig:
    """A config for the fake echo server, with the extra flags appended."""
    return ServerConfig(
        Name=name,
        Command=sys.executable,
        Args=["-m", "tests.helpers.mcp_echo_server", *extra],
        Env={"PYTHONPATH": str(REPO_ROOT)},
        ConnectTimeout=5.0,
        CallTimeout=5.0,
    )


@pytest.mark.asyncio
async def test_mcp_stdio_discovers_and_calls_tool() -> None:
    manager = await Connect(LiveContext(), [fake_config("fake")])
    try:
        discovered = manager.Tools()
        assert len(discovered) == 1
        spec = discovered[0].Specs()[0]
        assert spec.Name == "echo"
        assert spec.Description == "Echo text"
        assert spec.Risky
        assert spec.Parameters is not None
        assert spec.Parameters["type"] == "object"

        registry = NewRegistry()
        registry.Add(*discovered)
        result = await registry.Run(LiveContext(), ToolCall(Name="echo", Input='{"text":"hello"}'))
        assert result == "echo: hello"
    finally:
        await manager.Close()


@pytest.mark.asyncio
async def test_mcp_manager_adds_removes_and_restarts_servers() -> None:
    manager = await Connect(LiveContext(), [])
    try:
        added = await manager.Add(LiveContext(), fake_config("dynamic"))
        assert len(added) == 1

        servers = manager.Servers()
        assert len(servers) == 1
        assert servers[0].Name == "dynamic"
        assert list(servers[0].Tools) == ["echo"]

        seen: dict[str, int] = {}

        def replace(old_names: list[str], replacement: list[RemoteTool]) -> None:
            seen["old"] = len(old_names)
            seen["new"] = len(replacement)

        await manager.Restart(LiveContext(), "dynamic", replace)
        assert seen == {"old": 1, "new": 1}

        removed = await manager.Remove("dynamic")
        assert removed == ["echo"]
        assert manager.Tools() == []

        with pytest.raises(RuntimeError):
            await manager.Remove("dynamic")
    finally:
        await manager.Close()


@pytest.mark.asyncio
async def test_mcp_connect_normalizes_server_failure() -> None:
    config = ServerConfig(
        Name="broken",
        Command=sys.executable,
        Args=["-c", "pass"],
        ConnectTimeout=1.0,
    )

    with pytest.raises(RuntimeError, match='MCP server "broken"'):
        await Connect(LiveContext(), [config])


@pytest.mark.asyncio
async def test_mcp_manager_rejects_a_duplicated_server(tmp_path: Path) -> None:
    manager = await Connect(LiveContext(), [])
    try:
        await manager.Add(LiveContext(), fake_config("dynamic"))
        with pytest.raises(RuntimeError, match="is duplicated"):
            await manager.Add(LiveContext(), fake_config("dynamic"))
        assert len(manager.Servers()) == 1
    finally:
        await manager.Close()


@pytest.mark.asyncio
async def test_mcp_call_has_a_deadline() -> None:
    config = fake_config("slow", "--call-delay", "1.0")
    config.CallTimeout = 0.1
    manager = await Connect(LiveContext(), [config])
    try:
        tool = manager.Tools()[0]
        with pytest.raises(RuntimeError, match=r"call MCP tool slow\.echo"):
            await tool.Run(LiveContext(), ToolCall(Name="echo", Input='{"text":"hello"}'))
    finally:
        await manager.Close()


def test_mcp_environment_keeps_only_basic_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setenv("SECRET_TOKEN", "leak")

    environment = command_environment({"PYTHONPATH": "/repo"})

    assert environment["PATH"] == "/usr/bin"
    assert environment["HOME"] == "/home/tester"
    assert environment["PYTHONPATH"] == "/repo"
    assert "SECRET_TOKEN" not in environment


def test_mcp_tool_specs_are_always_risky() -> None:
    spec = tool_spec({"name": "x", "description": "d", "inputSchema": {"type": "object", "properties": {}}})

    assert spec.Risky
    assert spec.Parameters == {"type": "object", "properties": {}}


def test_mcp_result_at_the_limit_is_not_truncated() -> None:
    text = "a" * max_result_bytes

    assert format_result({"content": [{"type": "text", "text": text}]}) == text
    assert format_result({"content": [{"type": "text", "text": text + "a"}]}).endswith("... truncated")


def test_mcp_result_truncates_on_a_rune_boundary() -> None:
    # Two bytes per character, so a byte cut would land mid-rune.
    text = "é" * max_result_bytes

    output = format_result({"content": [{"type": "text", "text": text}]})

    assert output.endswith("\n... truncated")
    assert "\ufffd" not in output
    assert len(output.encode("utf-8")) <= max_result_bytes + len("\n... truncated")


def test_mcp_empty_error_result_reports_an_error() -> None:
    assert format_result({"content": [], "isError": True}) == "MCP tool reported an error"


@pytest.mark.asyncio
async def test_mcp_manager_rejects_add_after_close() -> None:
    manager = Manager()
    await manager.Close()

    with pytest.raises(RuntimeError, match="MCP manager is closed"):
        await manager.Add(LiveContext(), fake_config("late"))
