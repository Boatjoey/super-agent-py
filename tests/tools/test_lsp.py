"""The LSP tools, plus the framing and reconnect cases.

The server is :mod:`tests.helpers.lsp_fake_server`, run as ``python -m``;
every test drives it over a pipe and none of them touches the network.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from super_agent.errors import Cancelled
from super_agent.runtime.protocol.run_context import RunContext, live_context
from super_agent.runtime.protocol.types import ToolCall
from super_agent.tools.lsp import Manager, ServerConfig, connect
from super_agent.tools.lsp.client import max_message_bytes, read_header
from tests.tools.test_workspace import workspace_for

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_ORDER = ["lsp_diagnostics", "lsp_symbols", "lsp_definition", "lsp_references", "lsp_outline"]


@pytest.fixture(autouse=True)
def _helper_is_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``tests.helpers`` importable by the child process.

    The language server inherits the test's environment, and the deterministic
    fixture has already moved the working directory away from the repository, so
    the child needs ``PYTHONPATH`` to find the helper module.
    """
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))


def server_config(*extra: str) -> ServerConfig:
    """A config for the fake server, with the extra flags appended."""
    return ServerConfig(
        name="fake",
        command=sys.executable,
        args=("-m", "tests.helpers.lsp_fake_server", *extra),
        extensions=("go",),
        language_id="go",
    )


def call_for(name: str, **arguments: object) -> ToolCall:
    """A tool call for ``name`` with the standard argument set."""
    payload = {"path": "main.go", "line": 1, "column": 1, "query": "Main", **arguments}
    return ToolCall(name=name, input=json.dumps(payload))


def write_main(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.go").write_text("package main\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_lsp_tools_query_configured_server(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_main(root)
    manager = await connect(live_context(), workspace_for(root), [server_config()])
    try:
        tools = manager.tools()
        assert [tool.specs()[0].name for tool in tools] == TOOL_ORDER
        for tool in tools:
            name = tool.specs()[0].name
            result = await tool.run(live_context(), call_for(name))
            assert result not in ("", "null"), name
    finally:
        await manager.close()


def test_lsp_manager_exposes_no_tools_without_configs(tmp_path: Path) -> None:
    manager = Manager(workspace_for(tmp_path), [])
    assert manager.tools() == []


@pytest.mark.asyncio
async def test_lsp_rejects_a_path_no_server_covers(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_main(root)
    (root / "notes.txt").write_text("hello", encoding="utf-8")
    manager = await connect(live_context(), workspace_for(root), [server_config()])
    try:
        tool = manager.tools()[2]
        with pytest.raises(RuntimeError, match=r"no LSP server configured for \.txt"):
            await tool.run(live_context(), call_for("lsp_definition", path="notes.txt"))
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_lsp_rejects_a_path_outside_the_workspace(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_main(root)
    (tmp_path / "outside.go").write_text("package outside\n", encoding="utf-8")
    manager = await connect(live_context(), workspace_for(root), [server_config()])
    try:
        tool = manager.tools()[2]
        with pytest.raises(RuntimeError, match="outside readable workspace roots"):
            await tool.run(live_context(), call_for("lsp_definition", path="../outside.go"))
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_lsp_read_header_returns_a_valid_length() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"Content-Length: 12\r\n\r\n")

    assert await read_header(reader) == 12


@pytest.mark.asyncio
async def test_lsp_read_header_rejects_an_oversize_message() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(f"Content-Length: {max_message_bytes + 1}\r\n\r\n".encode())

    with pytest.raises(RuntimeError, match=f"exceeds the {max_message_bytes} byte limit"):
        await read_header(reader)


@pytest.mark.asyncio
async def test_lsp_read_header_rejects_a_missing_length() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"Content-Type: application/json\r\n\r\n")

    with pytest.raises(RuntimeError, match="invalid LSP content length"):
        await read_header(reader)


@pytest.mark.asyncio
async def test_lsp_diagnostics_settle_before_being_read(tmp_path: Path) -> None:
    """A diagnostic pushed shortly after ``didOpen`` must still be returned."""
    root = tmp_path / "root"
    write_main(root)
    manager = await connect(live_context(), workspace_for(root), [server_config("--diagnostics-delay", "0.15")])
    try:
        tool = manager.tools()[0]
        result = await tool.run(live_context(), call_for("lsp_diagnostics"))
        diagnostics = json.loads(result)
        assert diagnostics
        assert diagnostics[0]["message"] == "fake diagnostic"
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_lsp_client_reconnects_when_the_working_directory_changes(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    write_main(first)
    write_main(second)
    workspace = _SwitchableWorkspace(os.path.realpath(first))
    manager = await connect(live_context(), workspace, [server_config()])
    try:
        tool = manager.tools()[0]
        assert os.path.realpath(first) in await tool.run(live_context(), call_for("lsp_diagnostics"))
        workspace.cwd = os.path.realpath(second)
        assert os.path.realpath(second) in await tool.run(live_context(), call_for("lsp_diagnostics"))
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_lsp_cancelling_a_request_leaves_the_client_usable(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write_main(root)
    manager = await connect(live_context(), workspace_for(root), [server_config("--delay", "0.25")])
    try:
        tool = manager.tools()[4]
        call = call_for("lsp_outline")
        ctx = RunContext()
        task = asyncio.ensure_future(tool.run(ctx, call))
        await asyncio.sleep(0.05)
        ctx.cancel()
        with pytest.raises(Cancelled):
            await task
        # The late reply lands in a slot the client has already dropped; the
        # read loop must survive it and the client must still answer.
        await asyncio.sleep(0.4)
        assert await tool.run(live_context(), call) not in ("", "null")
    finally:
        await manager.close()


class _SwitchableWorkspace:
    """A ``WorkspaceContext`` whose working directory can move mid-test."""

    def __init__(self, cwd: str) -> None:
        self.cwd = os.path.realpath(cwd)

    def get_primary_root(self) -> str:
        return self.cwd

    def get_cwd(self) -> str:
        return self.cwd

    def resolve_path(self, path: str) -> str:
        if not os.path.isabs(path):
            path = os.path.join(self.cwd, path)
        return os.path.normpath(path)

    def can_read(self, path: str) -> bool:
        return True

    def can_write(self, path: str) -> bool:
        return True
