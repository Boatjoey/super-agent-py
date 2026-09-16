"""The workspace policy every built-in tool test runs against.

Ported from ``tests/tools/workspace_test.go``, which supplies ``testWorkspace``
to the rest of the package. The real ``super_agent.workspace.Context`` is
assembled by the composition root and is exercised by the workspace adapter's
own tests, so its observable behaviour — canonical containment over one or more
roots, per-root access modes, and resolution through the nearest existing
ancestor — is reproduced here as a test double.

The tests in this module cover ``tools/workspace.go`` itself: the readable and
writable resolution helpers and the display path they return.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from super_agent.tools.workspace import resolve_readable, resolve_writable

ACCESS_READ = "read"
ACCESS_READ_WRITE = "read_write"


@dataclasses.dataclass(frozen=True, slots=True)
class Root:
    """One access root, mirroring ``workspace.Root``."""

    Path: str
    Access: str


class FakeWorkspace:
    """A ``WorkspaceContext`` with the real policy's containment rules."""

    def __init__(self, primary_root: str, cwd: str, roots: list[Root]) -> None:
        if primary_root == "" or cwd == "":
            raise RuntimeError("workspace primary root and cwd are required")
        normalized: list[Root] = []
        for root in roots:
            if root.Access not in (ACCESS_READ, ACCESS_READ_WRITE):
                raise RuntimeError("invalid workspace root access")
            normalized.append(Root(Path=_canonical_directory(root.Path), Access=root.Access))
        if not normalized:
            raise RuntimeError("workspace requires at least one root")
        self._primary_root = _canonical_directory(primary_root)
        self._cwd = _canonical_directory(cwd)
        self._roots = normalized
        if not self._can_access(self._primary_root, write=False):
            raise RuntimeError("workspace primary root is not readable")
        if not self._can_access(self._cwd, write=False):
            raise RuntimeError("workspace cwd is not readable")

    def GetPrimaryRoot(self) -> str:
        return self._primary_root

    def GetCWD(self) -> str:
        return self._cwd

    def ResolvePath(self, path: str) -> str:
        """Resolve relative to the workspace cwd and canonicalize symlinks."""
        if path == "":
            raise RuntimeError("path is required")
        if not os.path.isabs(path):
            path = os.path.join(self._cwd, path)
        return _canonical_nearest(os.path.abspath(path))

    def CanRead(self, path: str) -> bool:
        return self._can_access_resolved(path, write=False)

    def CanWrite(self, path: str) -> bool:
        return self._can_access_resolved(path, write=True)

    def _can_access_resolved(self, path: str, *, write: bool) -> bool:
        try:
            resolved = self.ResolvePath(path)
        except OSError:
            return False
        return self._can_access(resolved, write=write)

    def _can_access(self, path: str, *, write: bool) -> bool:
        for root in self._roots:
            if write and root.Access != ACCESS_READ_WRITE:
                continue
            relative = os.path.relpath(path, root.Path)
            if relative == ".." or relative.startswith(".." + os.sep):
                continue
            if os.path.isabs(relative):
                continue
            return True
        return False


def NewContext(primary_root: str | Path, cwd: str | Path, roots: list[Root]) -> FakeWorkspace:
    """Build a workspace over explicit roots, as ``workspace.NewContext`` does."""
    return FakeWorkspace(str(primary_root), str(cwd), roots)


def NewDefaultContext(root: str | Path) -> FakeWorkspace:
    """Build a workspace whose single root is also its cwd."""
    return NewContext(root, root, [Root(Path=str(root), Access=ACCESS_READ_WRITE)])


def workspace_for(root: str | Path | None = None) -> FakeWorkspace:
    """The default workspace for ``root``, or for the process cwd."""
    return NewDefaultContext(root if root is not None else os.getcwd())


def _canonical_directory(path: str) -> str:
    resolved = os.path.realpath(os.path.abspath(path), strict=True)
    if not os.path.isdir(resolved):
        raise RuntimeError("workspace root is not a directory")
    return os.path.normpath(resolved)


def _canonical_nearest(path: str) -> str:
    """Resolve symlinks on the longest existing prefix and re-append the rest."""
    suffix = ""
    current = os.path.normpath(path)
    while True:
        try:
            resolved = os.path.realpath(current, strict=True)
        except OSError:
            parent = os.path.dirname(current)
            if parent == current:
                return os.path.normpath(os.path.join(current, suffix))
            suffix = os.path.join(os.path.basename(current), suffix)
            current = parent
        else:
            return os.path.normpath(os.path.join(resolved, suffix))


def test_resolve_readable_returns_a_path_relative_to_the_workspace(tmp_path: Path) -> None:
    workspace = workspace_for(tmp_path)
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "a.txt").write_text("hello", encoding="utf-8")

    resolved, display = resolve_readable(workspace, "nested/a.txt")

    assert resolved == os.path.join(os.path.realpath(tmp_path), "nested", "a.txt")
    assert display == "nested/a.txt"


def test_resolve_writable_rejects_a_read_only_root(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    read_only = tmp_path / "readonly"
    primary.mkdir()
    read_only.mkdir()
    (read_only / "shared.txt").write_text("shared", encoding="utf-8")
    workspace = NewContext(
        primary,
        primary,
        [Root(Path=str(primary), Access=ACCESS_READ_WRITE), Root(Path=str(read_only), Access=ACCESS_READ)],
    )
    shared = str(read_only / "shared.txt")

    _, display = resolve_readable(workspace, shared)
    assert display == os.path.join("..", "readonly", "shared.txt")
    with pytest.raises(RuntimeError, match="outside writable workspace roots"):
        resolve_writable(workspace, shared)


def test_resolution_refuses_a_path_outside_every_root(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")
    workspace = workspace_for(worktree)

    with pytest.raises(RuntimeError, match="outside readable workspace roots"):
        resolve_readable(workspace, "../secret.txt")
    with pytest.raises(RuntimeError, match="outside writable workspace roots"):
        resolve_writable(workspace, "../secret.txt")


def test_resolution_refuses_a_workspace_that_is_not_configured() -> None:
    with pytest.raises(RuntimeError, match="workspace is not configured"):
        resolve_readable(None, "a.txt")
