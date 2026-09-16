"""The narrow workspace policy port that built-in tools require.

Ported from ``tools/workspace.go``. ``super_agent.workspace.Context`` is the
concrete implementation assembled by ``app``; a tool only ever sees this
protocol, so the same tool values run against the real context or a test double.

Go returns ``(value, error)``; here a refusal raises, and the caller turns it
into the text it feeds back to the model.
"""

from __future__ import annotations

import os
from typing import Protocol


class WorkspaceContext(Protocol):
    """Filesystem access policy for one agent session, as tools see it.

    Access roots deliberately do not imply trust for configuration discovery:
    this port answers only "where may this call read and write".
    """

    def GetPrimaryRoot(self) -> str:
        """The root a sandbox binds read-write."""
        ...

    def GetCWD(self) -> str:
        """The directory a command or relative path starts from."""
        ...

    def ResolvePath(self, path: str) -> str:
        """Canonicalize ``path``; raises when it cannot be resolved."""
        ...

    def CanRead(self, path: str) -> bool:
        """Whether ``path`` falls inside a readable root."""
        ...

    def CanWrite(self, path: str) -> bool:
        """Whether ``path`` falls inside a writable root."""
        ...


def resolve_readable(workspace: WorkspaceContext | None, path: str) -> tuple[str, str]:
    """Return ``(resolved, display)`` for a path the caller may read."""
    if workspace is None:
        raise RuntimeError("workspace is not configured")
    resolved = workspace.ResolvePath(path)
    if not workspace.CanRead(resolved):
        raise RuntimeError("path is outside readable workspace roots")
    return resolved, display_path(workspace, resolved)


def resolve_writable(workspace: WorkspaceContext | None, path: str) -> tuple[str, str]:
    """Return ``(resolved, display)`` for a path the caller may write."""
    if workspace is None:
        raise RuntimeError("workspace is not configured")
    resolved = workspace.ResolvePath(path)
    if not workspace.CanWrite(resolved):
        raise RuntimeError("path is outside writable workspace roots")
    return resolved, display_path(workspace, resolved)


def display_path(workspace: WorkspaceContext, path: str) -> str:
    """The path as the model should see it: relative to the workspace cwd."""
    try:
        relative = os.path.relpath(path, workspace.GetCWD())
    except ValueError:
        return path.replace(os.sep, "/")
    return relative.replace(os.sep, "/")
