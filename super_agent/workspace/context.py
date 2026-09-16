"""The single filesystem access policy for one agent session.

A :class:`Context` holds a canonical primary root, a canonical cwd, and the roots
the session may reach with their access modes, and it answers the only three
questions tools need: where a path resolves to, whether it may be read, and
whether it may be written.

Access roots deliberately do not imply trust for configuration discovery: the
roots here grant file access only, and project instructions are still loaded from
the selected config root.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Sequence
from typing import Final


class Access(str):
    """Whether a workspace root may be written to. A ``str`` subclass so it serialises as its value."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Access({str.__repr__(self)})"


ACCESS_READ: Final[Access] = Access("read")
ACCESS_READ_WRITE: Final[Access] = Access("read_write")


@dataclasses.dataclass(frozen=True, slots=True)
class Root:
    """One root the workspace may reach, and how."""

    path: str = ""
    access: Access = ACCESS_READ


class Context:
    """Canonical containment, access modes, and cwd resolution.

    Every path this object returns is canonical: symlinks are resolved and, for a
    target that does not exist yet, the nearest existing ancestor is resolved
    before the remaining components are re-appended.
    """

    def __init__(self, primaryRoot: str, cwd: str, roots: Sequence[Root]) -> None:
        self._primaryRoot = primaryRoot
        self._cwd = cwd
        self._roots = tuple(roots)

    def get_primary_root(self) -> str:
        """The canonical primary root."""
        return self._primaryRoot

    def get_cwd(self) -> str:
        """The canonical directory relative paths resolve against."""
        return self._cwd

    def get_roots(self) -> list[Root]:
        """A copy of the roots, so a caller cannot mutate the policy."""
        return list(self._roots)

    def resolve_path(self, path: str) -> str:
        """Resolve ``path`` against the cwd and canonicalize it."""
        if path == "":
            raise ValueError("path is required")
        if not os.path.isabs(path):
            path = os.path.join(self._cwd, path)
        return canonicalNearest(os.path.abspath(path))

    def can_read(self, path: str) -> bool:
        """Whether ``path`` lies inside a readable root."""
        try:
            resolved = self.resolve_path(path)
        except (OSError, ValueError):
            return False
        return self.canAccess(resolved, write=False)

    def can_write(self, path: str) -> bool:
        """Whether ``path`` lies inside a writable root."""
        try:
            resolved = self.resolve_path(path)
        except (OSError, ValueError):
            return False
        return self.canAccess(resolved, write=True)

    def canAccess(self, path: str, write: bool) -> bool:
        """The containment check: a lexically relative path that never escapes."""
        for root in self._roots:
            if write and root.access != ACCESS_READ_WRITE:
                continue
            try:
                relative = os.path.relpath(path, root.path)
            except ValueError:
                continue
            if relative != ".." and not relative.startswith(".." + os.sep) and not os.path.isabs(relative):
                return True
        return False


def new_context(primaryRoot: str, cwd: str, roots: Sequence[Root]) -> Context:
    """Build a validated context, or raise.

    Every path is canonicalized and the primary root and cwd must be readable by
    the resulting policy, so a context that exists is one that works.
    """
    if primaryRoot == "" or cwd == "":
        raise ValueError("workspace primary root and cwd are required")
    primary = canonicalDirectory(primaryRoot)
    working = canonicalDirectory(cwd)
    normalized: list[Root] = []
    for root in roots:
        if root.access != ACCESS_READ and root.access != ACCESS_READ_WRITE:
            raise ValueError("invalid workspace root access")
        normalized.append(Root(path=canonicalDirectory(root.path), access=root.access))
    if not normalized:
        raise ValueError("workspace requires at least one root")
    context = Context(primary, working, normalized)
    if not context.canAccess(primary, write=False):
        raise ValueError("workspace primary root is not readable")
    if not context.canAccess(working, write=False):
        raise ValueError("workspace cwd is not readable")
    return context


def new_default_context(root: str) -> Context:
    """One read-write root at ``root``, with the cwd there too."""
    return new_context(root, root, [Root(path=root, access=ACCESS_READ_WRITE)])


def canonicalDirectory(path: str) -> str:
    """Resolve symlinks and require an existing directory, like ``filepath.EvalSymlinks`` plus a stat."""
    resolved = os.path.realpath(os.path.abspath(path), strict=True)
    if not os.path.isdir(resolved):
        raise NotADirectoryError("workspace root is not a directory: " + path)
    return os.path.normpath(resolved)


def canonicalNearest(path: str) -> str:
    """Canonicalize the nearest existing ancestor and re-append the rest.

    A path that does not exist yet still has to be checked against the roots, so
    the missing components are kept verbatim while their parent is resolved.
    """
    suffix = ""
    current = os.path.normpath(path)
    while True:
        try:
            resolved = os.path.realpath(current, strict=True)
        except FileNotFoundError:
            parent = os.path.dirname(current)
            if parent == current:
                return os.path.normpath(os.path.join(current, suffix))
            suffix = os.path.join(os.path.basename(current), suffix)
            current = parent
        else:
            return os.path.normpath(os.path.join(resolved, suffix))
