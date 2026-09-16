"""Project root resolution.

:class:`Project` identifies the code project the user selected, and its id is
intentionally independent of workspace access roots: which directory the agent
may read is a different question from which project the configuration and history
belong to.
"""

from __future__ import annotations

import dataclasses
import os


@dataclasses.dataclass(frozen=True, slots=True)
class Project:
    """The selected project: its stable id and its canonical root."""

    id: str = ""
    root: str = ""


def resolve(explicit: str, cwd: str) -> Project:
    """Select an explicit directory, otherwise walk up to the nearest ``.git``.

    An explicitly selected directory wins outright. Without one, resolution walks
    from ``cwd`` toward the filesystem root and chooses the first directory
    containing ``.git``, falling back to ``cwd`` itself.
    """
    if cwd == "":
        raise ValueError("cwd is required")
    start = cwd
    if explicit != "":
        start = explicit if os.path.isabs(explicit) else os.path.join(cwd, explicit)
    root = canonicalDirectory(start)
    if explicit == "":
        current = root
        while True:
            try:
                os.stat(os.path.join(current, ".git"))
            except FileNotFoundError:
                parent = os.path.dirname(current)
                if parent == current:
                    break
                current = parent
            except OSError:
                raise
            else:
                root = current
                break
    return Project(id=root, root=root)


def canonicalDirectory(path: str) -> str:
    """Resolve symlinks and require an existing directory."""
    resolved = os.path.realpath(os.path.abspath(path), strict=True)
    if not os.path.isdir(resolved):
        raise NotADirectoryError("project root is not a directory: " + path)
    return os.path.normpath(resolved)
