"""Workspace access policy: containment, access modes, and symlink escapes.

Ported from ``tests/workspace/context_test.go``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from super_agent import workspace


def test_context_allows_relative_and_absolute_paths_inside_workspace(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    context = new_context(str(root), workspace.Root(Path=str(root), Access=workspace.AccessReadWrite))

    relative = context.ResolvePath("src/index.ts")
    want = os.path.join(real(root), "src", "index.ts")

    assert relative == want
    assert context.CanRead("src/index.ts")
    assert context.CanWrite(want)


def test_context_rejects_traversal_prefix_collision_and_outside_path(tmp_path: Path) -> None:
    parent = tmp_path
    root = parent / "project"
    secret = parent / "project-secret"
    outside = parent / "outside"
    for directory in (root, secret, outside):
        directory.mkdir()
    context = new_context(str(root), workspace.Root(Path=str(root), Access=workspace.AccessReadWrite))

    for path in ("../project-secret/file", str(secret / "file"), str(outside)):
        assert not context.CanRead(path), path
        assert not context.CanWrite(path), path


def test_context_honors_read_only_read_write_and_additional_roots(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    read_only = tmp_path / "readonly"
    additional = tmp_path / "additional"
    for directory in (primary, read_only, additional):
        directory.mkdir()
    context = new_context(
        str(primary),
        workspace.Root(Path=str(primary), Access=workspace.AccessReadWrite),
        workspace.Root(Path=str(read_only), Access=workspace.AccessRead),
        workspace.Root(Path=str(additional), Access=workspace.AccessReadWrite),
    )

    assert context.CanRead(str(read_only / "file"))
    assert not context.CanWrite(str(read_only / "file"))
    assert context.CanRead(str(additional / "file"))
    assert context.CanWrite(str(additional / "file"))
    assert context.GetPrimaryRoot() == real(primary)
    assert context.GetCWD() == real(primary)
    assert len(context.GetRoots()) == 3


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks commonly need elevated privileges on Windows")
def test_context_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    for directory in (root, outside):
        directory.mkdir()
    os.symlink(outside, root / "link")
    context = new_context(str(root), workspace.Root(Path=str(root), Access=workspace.AccessReadWrite))

    assert not context.CanRead("link/secret")
    assert not context.CanWrite("link/new-file")


def new_context(primary: str, *roots: workspace.Root) -> workspace.Context:
    """Mirror the Go test helper: the primary root is also the cwd."""
    return workspace.NewContext(primary, primary, roots)


def real(path: Path) -> str:
    """The canonical spelling, which is what a context stores."""
    return os.path.realpath(str(path))
