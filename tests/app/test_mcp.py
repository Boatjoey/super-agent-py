"""The MCP controller's lifecycle and settings persistence.

The server is :mod:`tests.helpers.mcp_echo_server`, run as ``python -m``. That is
why the controller's workspace is the repository root: ``python -m`` resolves the
module against the process working directory, and ``MCPController.add`` builds a
server configuration with no environment of its own.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from super_agent.app import default_settings, load_settings_file, new_mcp_controller, save_settings_file
from super_agent.runtime.protocol.run_context import live_context
from super_agent.tools import new_registry
from super_agent.tools.mcp import connect

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
    manager = await connect(live_context(), [])
    try:
        registry = new_registry()
        settingsPath = tmp_path / "settings.json"
        save_settings_file(str(settingsPath), default_settings())
        controller = new_mcp_controller(manager, registry, str(settingsPath), str(REPO_ROOT), None)

        await controller.add(live_context(), "fake", sys.executable, ["-m", "tests.helpers.mcp_echo_server"])
        assert len(controller.list()) == 1
        assert len(registry.specs()) == 1

        settings = load_settings_file(str(settingsPath))
        assert settings.mcp_servers["fake"].command == sys.executable

        await controller.remove("fake")
        settings = load_settings_file(str(settingsPath))
        assert len(settings.mcp_servers) == 0
        assert len(registry.specs()) == 0
    finally:
        await manager.close()
