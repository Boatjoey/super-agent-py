"""The workspace file tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from super_agent.runtime.protocol.run_context import LiveContext
from super_agent.runtime.protocol.types import ToolCall
from super_agent.tools import (
    DefaultRegistry,
    Registry,
    RegistryForWorkspace,
    SandboxConfig,
    SandboxedRegistry,
    SandboxModeOff,
)
from super_agent.tools.files import max_tool_output_lines
from tests.tools.test_workspace import ACCESS_READ, ACCESS_READ_WRITE, NewContext, Root, workspace_for


def must_write(root: Path, relative: str, content: str) -> None:
    """Write ``content`` under ``root``, creating parent directories."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def sandboxed_registry_for(root: Path) -> Registry:
    """A registry whose file and command tools are jailed to ``root``.

    This is how a delegation worktree is jailed.
    """
    return SandboxedRegistry(
        SandboxConfig(Mode=SandboxModeOff, Workspace=str(root)),
        workspace_for(root),
    )


async def must_succeed(registry: Registry, name: str, payload: dict[str, Any]) -> str:
    return await registry.Run(LiveContext(), ToolCall(Name=name, Input=json.dumps(payload)))


async def must_fail(registry: Registry, name: str, payload: dict[str, Any]) -> None:
    with pytest.raises(RuntimeError):
        await registry.Run(LiveContext(), ToolCall(Name=name, Input=json.dumps(payload)))


def test_file_tools_expose_the_first_priority_tools(tmp_path: Path) -> None:
    names = {spec.Name for spec in DefaultRegistry(workspace_for(tmp_path)).Specs()}

    for name in ("read_file", "list_files", "search", "apply_patch", "write_file", "bash"):
        assert name in names


@pytest.mark.asyncio
async def test_read_file_supports_a_line_range(tmp_path: Path) -> None:
    must_write(tmp_path, "notes.txt", "one\ntwo\nthree\n")
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await must_succeed(registry, "read_file", {"path": "notes.txt", "start_line": 2, "end_line": 3})

    assert got == "2: two\n3: three"


@pytest.mark.asyncio
async def test_read_file_rejects_a_path_outside_the_workspace(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    must_write(tmp_path, "secret.txt", "secret")
    registry = DefaultRegistry(workspace_for(worktree))

    await must_fail(registry, "read_file", {"path": "../secret.txt"})


@pytest.mark.asyncio
async def test_list_files_returns_matching_relative_files(tmp_path: Path) -> None:
    must_write(tmp_path, "a.py", "")
    must_write(tmp_path, "nested/b.py", "")
    must_write(tmp_path, "nested/c.txt", "")
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await must_succeed(registry, "list_files", {"path": ".", "pattern": "*.py"})

    assert got == "a.py\nnested/b.py"


@pytest.mark.asyncio
async def test_search_finds_text_with_line_numbers(tmp_path: Path) -> None:
    must_write(tmp_path, "a.txt", "alpha\nneedle\n")
    must_write(tmp_path, "nested/b.txt", "needle again\n")
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await must_succeed(registry, "search", {"query": "needle", "path": "."})

    assert got == "a.txt:2:needle\nnested/b.txt:1:needle again"


@pytest.mark.asyncio
async def test_apply_patch_replaces_expected_text(tmp_path: Path) -> None:
    must_write(tmp_path, "main.py", "def greet():\n    pass\n")
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await must_succeed(
        registry,
        "apply_patch",
        {"path": "main.py", "old_text": "    pass", "new_text": '    print("hi")'},
    )

    assert got == "patched main.py"
    assert 'print("hi")' in (tmp_path / "main.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_write_file_creates_parent_directories(tmp_path: Path) -> None:
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await must_succeed(registry, "write_file", {"path": "nested/out.txt", "content": "hello"})

    assert got == "wrote nested/out.txt"
    assert (tmp_path / "nested" / "out.txt").read_text(encoding="utf-8") == "hello"


@pytest.mark.asyncio
async def test_file_tools_use_the_injected_root_access(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    read_only = tmp_path / "readonly"
    primary.mkdir()
    read_only.mkdir()
    shared = read_only / "shared.txt"
    shared.write_text("shared", encoding="utf-8")
    registry = RegistryForWorkspace(
        NewContext(
            primary,
            primary,
            [Root(Path=str(primary), Access=ACCESS_READ_WRITE), Root(Path=str(read_only), Access=ACCESS_READ)],
        )
    )

    await must_succeed(registry, "read_file", {"path": str(shared)})
    await must_fail(registry, "write_file", {"path": str(shared), "content": "changed"})


@pytest.mark.asyncio
async def test_file_tools_anchor_to_the_injected_workspace(tmp_path: Path) -> None:
    # The workspace acts as a subagent worktree; the "parent" directory holds a
    # file the tool must refuse to reach.
    parent = tmp_path / "parent"
    worktree = parent / "worktree"
    worktree.mkdir(parents=True)
    must_write(parent, "secret.txt", "top secret")
    must_write(worktree, "local.txt", "local")
    registry = sandboxed_registry_for(worktree)

    await must_succeed(registry, "read_file", {"path": "local.txt"})
    await must_fail(registry, "read_file", {"path": "../secret.txt"})
    await must_fail(registry, "write_file", {"path": "../escape.txt", "content": "x"})
    assert not (parent / "escape.txt").exists()


@pytest.mark.asyncio
async def test_list_and_search_skip_symlinks_that_point_outside(tmp_path: Path) -> None:
    must_write(tmp_path, "real.txt", "findme")
    try:
        os.symlink("/etc", tmp_path / "etc")
    except OSError:
        pytest.skip("symlinks unavailable")
    registry = sandboxed_registry_for(tmp_path)

    listing = await must_succeed(registry, "list_files", {})
    assert "real.txt" in listing

    found = await must_succeed(registry, "search", {"query": "findme"})
    assert "real.txt:1" in found


@pytest.mark.asyncio
async def test_read_file_truncates_at_the_exact_limit(tmp_path: Path) -> None:
    content = "line\n" * (max_tool_output_lines + 50)
    must_write(tmp_path, "big.txt", content)
    registry = sandboxed_registry_for(tmp_path)

    output = await must_succeed(registry, "read_file", {"path": "big.txt"})

    lines = output.strip().split("\n")
    # maxToolOutputLines content lines plus the truncation marker.
    assert len(lines) == max_tool_output_lines + 1
    assert lines[-1].endswith("truncated")
