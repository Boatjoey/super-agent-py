"""Process termination on platforms without process groups.

Killing the direct child is the fallback; waiting on the process afterwards still
guards against a surviving grandchild holding the output pipes open.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Final

#: Bounds how long Wait keeps waiting for the output pipes to close after the
#: command itself has exited or been killed.
PROCESS_GROUP_WAIT_DELAY: Final[float] = 2.0

#: No process-group isolation exists to request at spawn time.
spawn_kwargs: Final[dict[str, Any]] = {}


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Kill the direct child, then wait a bounded time for its pipes to close."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), PROCESS_GROUP_WAIT_DELAY)
