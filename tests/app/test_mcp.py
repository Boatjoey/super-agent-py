"""The MCP controller's lifecycle and settings persistence.

The server is :mod:`tests.helpers.mcp_echo_server`, run as ``python -m``. That is
why the controller's workspace is the repository root: ``python -m`` resolves the
module against the process working directory, and ``MCPController.Add`` builds a
server configuration with no environment of its own.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from super_agent.app import DefaultSettings, LoadSettingsFile, NewMCPController, SaveSettingsFile
from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.tools import NewRegistry
from super_agent.tools.mcp import Connect

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "tests" / "helpers" / "mcp_echo_server.py"


def test_app_mcp_helper_process() -> None:
    """The fake server the next test connects to is present and runnable.

    The helper is executed directly rather than imported, so a missing or renamed
    module would otherwise make the controller test fail for a reason that has
    nothing to do with it.
    """
    assert HELPER.exists(), f"the fake MCP server is missing: {HELPER}"

    result = subprocess.run(
        [sys.executable, "-m", "tests.helpers.mcp_echo_server"],
        cwd=REPO_ROOT,
        input=b"",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode(errors="replace")


@pytest.mark.asyncio
async def test_mcp_controller_persists_add_and_remove(tmp_path: Path) -> None:
    manager = await Connect(LiveContext(), [])
    try:
        registry = NewRegistry()
        settingsPath = tmp_path / "settings.json"
        SaveSettingsFile(str(settingsPath), DefaultSettings())
        controller = NewMCPController(manager, registry, str(settingsPath), str(REPO_ROOT), None)

        await controller.Add(LiveContext(), "fake", sys.executable, ["-m", "tests.helpers.mcp_echo_server"])
        assert len(controller.List()) == 1
        assert len(registry.Specs()) == 1

        settings = LoadSettingsFile(str(settingsPath))
        assert settings.MCPServers["fake"].Command == sys.executable

        await controller.Remove("fake")
        settings = LoadSettingsFile(str(settingsPath))
        assert len(settings.MCPServers) == 0
        assert len(registry.Specs()) == 0
    finally:
        await manager.Close()
