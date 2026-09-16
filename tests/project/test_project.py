"""Project resolution: explicit selection, upward discovery, and cwd fallback.

Ported from ``tests/project/project_test.go``.
"""

from __future__ import annotations

import os
from pathlib import Path

from super_agent import project


def test_resolve_uses_explicit_directory(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"
    other = tmp_path / "other"
    explicit.mkdir()
    other.mkdir()

    got = project.Resolve(str(explicit), str(other))

    assert got.Root == os.path.realpath(str(explicit))
    assert got.ID != ""


def test_resolve_finds_nearest_git_ancestor(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    nested = root / "a" / "b"
    nested.mkdir(parents=True)

    got = project.Resolve("", str(nested))

    assert got.Root == os.path.realpath(str(root))


def test_resolve_falls_back_to_current_directory(tmp_path: Path) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    got = project.Resolve("", str(cwd))

    assert got.Root == os.path.realpath(str(cwd))
