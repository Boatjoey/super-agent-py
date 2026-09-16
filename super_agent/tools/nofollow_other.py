"""Open a workspace file on platforms without ``O_NOFOLLOW``.

Ported from ``tools/nofollow_other.go``. Containment relies on the symlink
resolution performed before these calls, so both helpers fall back to the plain
open, which cannot refuse a symlink final component.
"""

from __future__ import annotations

import os
from typing import IO


def open_workspace_file(path: str) -> IO[bytes]:
    """Open ``path`` read-only."""
    return open(path, "rb")


def write_file_no_follow(path: str, content: bytes, mode: int) -> None:
    """Write ``content`` to ``path``, creating it with ``mode``."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(descriptor, "wb") as file:
        file.write(content)
