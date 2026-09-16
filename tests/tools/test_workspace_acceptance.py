"""Black-box acceptance tests for the tool registry.

These drive the registry through its
public surface and assert only what the model would observe, so a change to the
containment implementation cannot quietly widen access.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from super_agent.runtime.protocol.run_context import live_context
from super_agent.runtime.protocol.types import ToolCall
from super_agent.tools import Registry, default_registry
from tests.tools.test_workspace import ACCESS_READ, ACCESS_READ_WRITE, Root, new_context, workspace_for

needs_git_and_gofmt = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("gofmt") is None,
    reason="git and gofmt are required",
)


def must_write(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


async def must_tool_succeed(registry: Registry, name: str, payload: dict[str, Any]) -> str:
    return await registry.run(live_context(), ToolCall(name=name, input=json.dumps(payload)))


async def must_tool_fail(registry: Registry, name: str, payload: dict[str, Any]) -> None:
    with pytest.raises(RuntimeError):
        await registry.run(live_context(), ToolCall(name=name, input=json.dumps(payload)))


@pytest.mark.asyncio
async def test_workspace_file_tools_black_box_acceptance(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    project = parent / "project"
    project_secret = parent / "project-secret"
    outside = parent / "outside"
    for directory in (project / "src", project / "allowed", project_secret, outside):
        directory.mkdir(parents=True)
    must_write(project, "src/a.txt", "before")
    must_write(parent, "secret.txt", "secret")
    must_write(project_secret, "foo", "secret")

    registry = default_registry(workspace_for(project))

    await must_tool_succeed(registry, "read_file", {"path": "src/a.txt"})
    await must_tool_succeed(registry, "write_file", {"path": "src/a.txt", "content": "after"})
    await must_tool_succeed(registry, "write_file", {"path": "allowed/new.txt", "content": "new"})
    await must_tool_succeed(registry, "read_file", {"path": str(project / "src" / "a.txt")})

    await must_tool_fail(registry, "read_file", {"path": "../secret.txt"})
    await must_tool_fail(registry, "write_file", {"path": "../secret.txt", "content": "no"})
    await must_tool_fail(registry, "read_file", {"path": str(project_secret / "foo")})
    await must_tool_fail(registry, "write_file", {"path": "foo/../../outside/planted.txt", "content": "no"})
    await must_tool_fail(registry, "read_file", {"path": str(outside / "missing")})

    if os.name == "posix":
        outside_file = outside / "external.txt"
        outside_file.write_text("external", encoding="utf-8")
        os.symlink(outside_file, project / "file-link")
        os.symlink(outside, project / "dir-link")
        await must_tool_fail(registry, "read_file", {"path": "file-link"})
        await must_tool_fail(registry, "search", {"path": "dir-link", "query": "external"})


@pytest.mark.asyncio
async def test_additional_read_only_root_black_box_acceptance(tmp_path: Path) -> None:
    project = tmp_path / "project"
    common = tmp_path / "common"
    project.mkdir()
    common.mkdir()
    shared = common / "shared.txt"
    shared.write_text("shared needle", encoding="utf-8")
    registry = default_registry(
        new_context(
            project,
            project,
            [Root(path=str(project), access=ACCESS_READ_WRITE), Root(path=str(common), access=ACCESS_READ)],
        )
    )

    await must_tool_succeed(registry, "read_file", {"path": str(shared)})
    await must_tool_succeed(registry, "search", {"path": str(common), "query": "needle"})
    await must_tool_fail(registry, "write_file", {"path": str(shared), "content": "changed"})
    await must_tool_fail(registry, "apply_patch", {"path": str(shared), "old_text": "shared", "new_text": "changed"})


@needs_git_and_gofmt
@pytest.mark.asyncio
async def test_command_tools_use_the_workspace_cwd_not_the_process_cwd(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    must_write(project, "main.go", 'package main\nfunc main(){println("x")}\n')
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    workspace = workspace_for(project)
    registry = default_registry(workspace)

    for name in ("run_command", "bash"):
        result = await must_tool_succeed(registry, name, {"command": "pwd"})
        assert result.strip() == workspace.get_cwd()

    status = await must_tool_succeed(registry, "git_status", {})
    assert "No commits yet" in status or "Initial commit" in status

    await must_tool_succeed(registry, "format", {"files": ["main.go"]})
    assert "func main() {" in (project / "main.go").read_text(encoding="utf-8")
