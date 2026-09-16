"""Port of ``tests/tools/command_tools_test.go``: the command-backed tools.

The Go tests call the real ``go``, ``gofmt``, and ``git`` binaries. These do the
same and skip when a binary is not installed, so the suite stays green on a
machine without a toolchain.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import ToolCall
from super_agent.tools import DefaultRegistry, Registry
from super_agent.tools.workspace import WorkspaceContext
from tests.tools.test_workspace import workspace_for

needs_go = pytest.mark.skipif(shutil.which("go") is None, reason="the go toolchain is not installed")
needs_gofmt = pytest.mark.skipif(shutil.which("gofmt") is None, reason="gofmt is not installed")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def must_write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


async def run(registry: Registry, name: str, payload: dict[str, Any]) -> str:
    return await registry.Run(LiveContext(), ToolCall(Name=name, Input=json.dumps(payload)))


def run_git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_default_registry_exposes_the_second_priority_tools(tmp_path: Path) -> None:
    names = {spec.Name for spec in DefaultRegistry(workspace_for(tmp_path)).Specs()}

    for name in ("run_command", "go_test", "format", "git_status", "git_diff"):
        assert name in names


@pytest.mark.asyncio
async def test_run_command_uses_the_workspace_cwd_and_truncates_output(tmp_path: Path) -> None:
    must_write(tmp_path, "nested/name.txt", "hello")
    registry = DefaultRegistry(workspace_for(tmp_path))
    payload = {
        "command": 'printf "%s:" "$(basename "$PWD")" && cat name.txt && printf abcdefghijklmnopqrstuvwxyz',
        "cwd": "nested",
        "max_output_bytes": 20,
    }

    got = await run(registry, "run_command", payload)

    assert "truncated" in got
    assert "nested:hello" in got


@pytest.mark.asyncio
async def test_run_command_rejects_a_cwd_outside_the_workspace(tmp_path: Path) -> None:
    registry = DefaultRegistry(workspace_for(tmp_path))

    with pytest.raises(RuntimeError, match="outside readable workspace roots"):
        await run(registry, "run_command", {"command": "pwd", "cwd": ".."})


@pytest.mark.asyncio
async def test_run_command_defaults_to_the_injected_workspace_cwd(tmp_path: Path) -> None:
    injected = tmp_path / "injected"
    injected.mkdir()
    process_cwd = tmp_path
    assert os.path.realpath(process_cwd) != os.path.realpath(injected)
    workspace: WorkspaceContext = workspace_for(injected)
    registry = DefaultRegistry(workspace)

    got = await run(registry, "run_command", {"command": "pwd"})

    assert got.strip() == os.path.realpath(injected)
    assert got.strip() == workspace.GetCWD()


@needs_go
@pytest.mark.asyncio
async def test_go_test_runs_packages(tmp_path: Path) -> None:
    must_write(tmp_path, "go.mod", "module example.com/x\n\ngo 1.21\n")
    must_write(tmp_path, "main.go", "package main\n")
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await run(registry, "go_test", {"packages": ["./..."]})

    assert "no test files" in got


@needs_gofmt
@pytest.mark.asyncio
async def test_format_runs_gofmt_on_workspace_files(tmp_path: Path) -> None:
    must_write(tmp_path, "main.go", 'package main\nfunc main(){println("hi")}\n')
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await run(registry, "format", {"files": ["main.go"]})

    assert got == "formatted 1 file"
    assert "func main() {" in (tmp_path / "main.go").read_text(encoding="utf-8")


@needs_git
@pytest.mark.asyncio
async def test_git_status_and_diff_are_read_only(tmp_path: Path) -> None:
    run_git(tmp_path, "init")
    must_write(tmp_path, "tracked.txt", "before\n")
    run_git(tmp_path, "add", "tracked.txt")
    must_write(tmp_path, "tracked.txt", "after\n")
    must_write(tmp_path, "new.txt", "new\n")
    registry = DefaultRegistry(workspace_for(tmp_path))

    status = await run(registry, "git_status", {})
    assert "AM tracked.txt" in status
    assert "?? new.txt" in status

    diff = await run(registry, "git_diff", {"paths": ["tracked.txt"]})
    assert "-before" in diff
    assert "+after" in diff
