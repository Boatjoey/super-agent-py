"""The narrow workspace policy port that built-in tools require.

``super_agent.workspace.Context`` is the concrete implementation assembled by
``app``; a tool only ever sees this protocol, so the same tool values run against
the real context or a test double.

A refusal raises, and the caller turns it into the text it feeds back to the
model.
"""

from __future__ import annotations

import os
from typing import Protocol


class WorkspaceContext(Protocol):
    """Filesystem access policy for one agent session, as tools see it.

    Access roots deliberately do not imply trust for configuration discovery:
    this port answers only "where may this call read and write".
    """

    def get_primary_root(self) -> str:
        """The root a sandbox binds read-write."""
        ...

    def get_cwd(self) -> str:
        """The directory a command or relative path starts from."""
        ...

    def resolve_path(self, path: str) -> str:
        """Canonicalize ``path``; raises when it cannot be resolved."""
        ...

    def can_read(self, path: str) -> bool:
        """Whether ``path`` falls inside a readable root."""
        ...

    def can_write(self, path: str) -> bool:
        """Whether ``path`` falls inside a writable root."""
        ...


def resolve_readable(workspace: WorkspaceContext | None, path: str) -> tuple[str, str]:
    """Return ``(resolved, display)`` for a path the caller may read."""
    if workspace is None:
        raise RuntimeError("workspace is not configured")
    resolved = workspace.resolve_path(path)
    if not workspace.can_read(resolved):
        raise RuntimeError("path is outside readable workspace roots")
    return resolved, display_path(workspace, resolved)


def resolve_writable(workspace: WorkspaceContext | None, path: str) -> tuple[str, str]:
    """Return ``(resolved, display)`` for a path the caller may write."""
    if workspace is None:
        raise RuntimeError("workspace is not configured")
    resolved = workspace.resolve_path(path)
    if not workspace.can_write(resolved):
        raise RuntimeError("path is outside writable workspace roots")
    return resolved, display_path(workspace, resolved)


def display_path(workspace: WorkspaceContext, path: str) -> str:
    """The path as the model should see it: relative to the workspace cwd."""
    try:
        relative = os.path.relpath(path, workspace.get_cwd())
    except ValueError:
        return path.replace(os.sep, "/")
    return relative.replace(os.sep, "/")
