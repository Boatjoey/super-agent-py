"""Process-group isolation for command tools.

Ported from ``tools/proc_unix.go``; the ``sys.platform`` dispatch in
``commands.py`` stands in for the Go ``unix`` build tag.

Go's ``isolateProcessGroup`` both puts the child in its own process group and
installs a cancel function. Here the group is created at spawn with
``start_new_session=True`` (see :data:`spawn_kwargs`) and the cancel function is
:func:`terminate_process_tree`.

Killing only the direct child is not enough: ``bash -lc "make"`` leaves the
compiler running, and ``bash -lc "sleep 300 &"`` leaves the background job
running, both after the tool call has already returned.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
from typing import Any, Final

#: Bounds how long Wait keeps waiting for the output pipes to close after the
#: command itself has exited or been killed.
PROCESS_GROUP_WAIT_DELAY: Final[float] = 2.0

#: Spawn options equivalent to Go's ``SysProcAttr{Setpgid: true}``.
spawn_kwargs: Final[dict[str, Any]] = {"start_new_session": True}


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    """Kill every process in the child's group, then wait a bounded time.

    A negative pid targets the whole group, so a grandchild that outlived the
    direct child dies with it. Waiting on the process afterwards is also what
    closes the output pipes: asyncio closes them when the child is reaped.
    """
    if process.returncode is None:
        # ProcessLookupError means the group is already gone.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), PROCESS_GROUP_WAIT_DELAY)
