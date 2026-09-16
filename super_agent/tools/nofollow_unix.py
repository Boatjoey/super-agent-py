"""Open a workspace file without following a symlink in its final component.

Ported from ``tools/nofollow_unix.go``; the ``sys.platform`` dispatch in
``files.py`` stands in for the Go ``unix`` build tag.
"""

from __future__ import annotations

import os
from typing import IO


def open_workspace_file(path: str) -> IO[bytes]:
    """Open ``path`` read-only, refusing a symlink final component.

    ``O_NOFOLLOW`` closes the final-component race: resolve-then-open spans two
    syscalls, and a path component swapped for a symlink in between would
    redirect the read outside the workspace. Deeper component swaps remain
    possible and are accepted as a documented residual risk.
    """
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    return os.fdopen(descriptor, "rb")


def write_file_no_follow(path: str, content: bytes, mode: int) -> None:
    """Write ``content`` to ``path``, refusing a symlink final component."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as file:
        file.write(content)
