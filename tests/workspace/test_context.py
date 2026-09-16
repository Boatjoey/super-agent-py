"""Workspace access policy: containment, access modes, and symlink escapes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from super_agent import workspace


def test_context_allows_relative_and_absolute_paths_inside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    context = new_context(str(root), workspace.Root(path=str(root), access=workspace.ACCESS_READ_WRITE))

    relative = context.resolve_path("src/index.ts")
    want = os.path.join(real(root), "src", "index.ts")

    assert relative == want
    assert context.can_read("src/index.ts")
    assert context.can_write(want)


def test_context_rejects_traversal_prefix_collision_and_outside_path(tmp_path: Path) -> None:
    parent = tmp_path
    root = parent / "project"
    secret = parent / "project-secret"
    outside = parent / "outside"
    for directory in (root, secret, outside):
        directory.mkdir()
    context = new_context(str(root), workspace.Root(path=str(root), access=workspace.ACCESS_READ_WRITE))

    for path in ("../project-secret/file", str(secret / "file"), str(outside)):
        assert not context.can_read(path), path
        assert not context.can_write(path), path


def test_context_honors_read_only_read_write_and_additional_roots(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    read_only = tmp_path / "readonly"
    additional = tmp_path / "additional"
    for directory in (primary, read_only, additional):
        directory.mkdir()
    context = new_context(
        str(primary),
        workspace.Root(path=str(primary), access=workspace.ACCESS_READ_WRITE),
        workspace.Root(path=str(read_only), access=workspace.ACCESS_READ),
        workspace.Root(path=str(additional), access=workspace.ACCESS_READ_WRITE),
    )

    assert context.can_read(str(read_only / "file"))
    assert not context.can_write(str(read_only / "file"))
    assert context.can_read(str(additional / "file"))
    assert context.can_write(str(additional / "file"))
    assert context.get_primary_root() == real(primary)
    assert context.get_cwd() == real(primary)
    assert len(context.get_roots()) == 3


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks commonly need elevated privileges on Windows")
def test_context_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    for directory in (root, outside):
        directory.mkdir()
    os.symlink(outside, root / "link")
    context = new_context(str(root), workspace.Root(path=str(root), access=workspace.ACCESS_READ_WRITE))

    assert not context.can_read("link/secret")
    assert not context.can_write("link/new-file")


def new_context(primary: str, *roots: workspace.Root) -> workspace.Context:
    """Build a context whose primary root is also its cwd."""
    return workspace.new_context(primary, primary, roots)


def real(path: Path) -> str:
    """The canonical spelling, which is what a context stores."""
    return os.path.realpath(str(path))
