"""Containment and resource bounds.

The symlink tests prove the workspace check resolves before it trusts, the
output tests prove a model-supplied limit cannot buffer an arbitrary amount of
memory, the environment tests prove credentials do not reach a child, and the
process-tree test proves a background job dies with the call that started it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
from pathlib import Path

import pytest

from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import ToolCall
from super_agent.tools import DefaultRegistry, DefaultSandboxConfig, SandboxedRegistry
from super_agent.tools.commands import max_output_bytes
from tests.helpers import spawn_tree
from tests.tools.test_workspace import workspace_for

REPO_ROOT = Path(__file__).resolve().parents[2]
TRUNCATION_MARKER = "\n... truncated"


@pytest.mark.asyncio
async def test_read_file_rejects_a_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret", encoding="utf-8")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    try:
        os.symlink(outside, worktree / "link")
    except OSError:
        pytest.skip("symlinks unavailable")
    registry = DefaultRegistry(workspace_for(worktree))

    # A lexical containment check accepts "link/secret.txt" because the
    # relative path contains no "..". Resolving the symlink first rejects it.
    with pytest.raises(RuntimeError, match="outside readable workspace roots"):
        await registry.Run(LiveContext(), ToolCall(Name="read_file", Input='{"path":"link/secret.txt"}'))


@pytest.mark.asyncio
async def test_write_file_rejects_a_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    try:
        os.symlink(outside, worktree / "link")
    except OSError:
        pytest.skip("symlinks unavailable")
    registry = DefaultRegistry(workspace_for(worktree))

    with pytest.raises(RuntimeError, match="outside writable workspace roots"):
        await registry.Run(
            LiveContext(),
            ToolCall(Name="write_file", Input='{"path":"link/planted.txt","content":"owned"}'),
        )
    assert not (outside / "planted.txt").exists()


@pytest.mark.asyncio
async def test_run_command_caps_the_requested_output_limit(tmp_path: Path) -> None:
    registry = DefaultRegistry(workspace_for(tmp_path))
    # The model asks for a 100 MB limit. Without a ceiling the command's entire
    # output is buffered in memory.
    payload = {
        "command": "head -c 400000 /dev/zero | tr '\\0' 'a'",
        "max_output_bytes": 100_000_000,
    }

    got = await registry.Run(LiveContext(), ToolCall(Name="run_command", Input=json.dumps(payload)))

    assert len(got) <= max_output_bytes + len(TRUNCATION_MARKER)
    assert "truncated" in got


@pytest.mark.asyncio
async def test_bash_truncates_output(tmp_path: Path) -> None:
    registry = DefaultRegistry(workspace_for(tmp_path))
    payload = {"command": "head -c 400000 /dev/zero | tr '\\0' 'a'"}

    got = await registry.Run(LiveContext(), ToolCall(Name="bash", Input=json.dumps(payload)))

    assert len(got) <= max_output_bytes + len(TRUNCATION_MARKER)


@pytest.mark.asyncio
async def test_run_command_hides_the_credential_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPER_AGENT_TEST_API_KEY", "sk-secret-value")
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await registry.Run(
        LiveContext(),
        ToolCall(Name="run_command", Input='{"command":"printenv SUPER_AGENT_TEST_API_KEY || echo absent"}'),
    )

    assert "sk-secret-value" not in got
    assert "absent" in got


@pytest.mark.asyncio
async def test_run_command_keeps_the_ordinary_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUPER_AGENT_TEST_PLAIN", "visible")
    registry = DefaultRegistry(workspace_for(tmp_path))

    # Scrubbing must not be so aggressive that ordinary variables disappear.
    got = await registry.Run(
        LiveContext(),
        ToolCall(Name="run_command", Input='{"command":"printenv SUPER_AGENT_TEST_PLAIN"}'),
    )

    assert "visible" in got


def _spawn_tree_command(marker: Path) -> str:
    return " ".join(
        [
            f"PYTHONPATH={shlex.quote(str(REPO_ROOT))}",
            shlex.quote(sys.executable),
            "-m",
            "tests.helpers.spawn_tree",
            shlex.quote(str(marker)),
        ]
    )


@pytest.mark.asyncio
async def test_run_command_starts_the_helper_grandchild(tmp_path: Path) -> None:
    """The process-tree test is only meaningful if the grandchild really runs."""
    registry = DefaultRegistry(workspace_for(tmp_path))
    payload = {
        "command": _spawn_tree_command(tmp_path / "probe"),
        "timeout_seconds": 1,
        # The output is what proves the tree started, so the timeout is not
        # raised here.
        "continue_on_error": True,
    }

    output = await registry.Run(LiveContext(), ToolCall(Name="run_command", Input=json.dumps(payload)))

    assert spawn_tree.READY_MARKER in output


@pytest.mark.asyncio
async def test_run_command_timeout_kills_the_process_tree(tmp_path: Path) -> None:
    registry = DefaultRegistry(workspace_for(tmp_path))
    marker = tmp_path / "survivor"

    # The backgrounded grandchild outlives the direct child. Killing only the
    # direct child would orphan it, and it would create the marker afterwards.
    with pytest.raises(TimeoutError):
        await registry.Run(
            LiveContext(),
            ToolCall(
                Name="run_command",
                Input=json.dumps({"command": _spawn_tree_command(marker), "timeout_seconds": 1}),
            ),
        )

    await asyncio.sleep(4)
    assert not marker.exists()


@pytest.mark.skipif(sys.platform == "linux", reason="Linux has a real strict sandbox")
def test_strict_sandbox_fails_closed_off_linux(tmp_path: Path) -> None:
    """Strict is the default, so an unsupported platform must refuse outright."""
    with pytest.raises(
        RuntimeError,
        match=re.escape("strict sandbox is currently supported only on Linux; set sandbox.mode to off explicitly"),
    ):
        SandboxedRegistry(DefaultSandboxConfig(str(tmp_path)), workspace_for(tmp_path))


@pytest.mark.skipif(sys.platform != "linux", reason="bubblewrap is Linux-only")
@pytest.mark.asyncio
async def test_strict_sandbox_requires_bubblewrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    with pytest.raises(RuntimeError, match="requires bubblewrap"):
        SandboxedRegistry(DefaultSandboxConfig(str(tmp_path)), workspace_for(tmp_path))


@pytest.mark.skipif(sys.platform != "linux", reason="bubblewrap is Linux-only")
@pytest.mark.asyncio
async def test_strict_sandbox_restricts_filesystem_network_and_resources(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    config = DefaultSandboxConfig(str(workspace))
    config.CPUSeconds = 7
    config.MemoryBytes = 64 << 20
    config.MaxProcesses = 17
    config.MaxOpenFiles = 23
    try:
        registry = SandboxedRegistry(config, workspace_for(workspace))
    except RuntimeError as err:
        pytest.skip(f"strict sandbox unavailable: {err}")

    try:
        probe = await registry.Run(LiveContext(), ToolCall(Name="run_command", Input='{"command":"printf ready"}'))
    except RuntimeError as err:
        pytest.skip(f"kernel namespaces unavailable: {err}")
    assert probe == "ready"

    inside = workspace / "inside"
    command = f"touch {inside}; touch {outside / 'planted'}"
    with pytest.raises(RuntimeError):
        await registry.Run(
            LiveContext(),
            ToolCall(Name="run_command", Input=json.dumps({"command": command})),
        )
    assert inside.exists()
    assert not (outside / "planted").exists()

    isolated = await registry.Run(
        LiveContext(),
        ToolCall(
            Name="run_command",
            Input=json.dumps({"command": "awk 'NR > 1 { exit 1 }' /proc/net/route && printf isolated"}),
        ),
    )
    assert "isolated" in isolated

    limits_command = 'printf \'%s %s %s\' "$(ulimit -v)" "$(ulimit -u)" "$(ulimit -n)"'
    limits = await registry.Run(
        LiveContext(),
        ToolCall(Name="run_command", Input=json.dumps({"command": limits_command})),
    )
    assert limits == "65536 17 23"
