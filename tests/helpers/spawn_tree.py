"""Spawn a process tree that only a process-group kill can stop.

Run as ``python -m tests.helpers.spawn_tree <marker>``. The parent sleeps long
enough to outlive any test timeout, and its child sleeps briefly and then
creates ``marker``. Killing only the direct child leaves the grandchild running,
so the marker appears a few seconds later; killing the whole process group — a
new session for the command tool — removes both.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

CHILD_DELAY_SECONDS = 3
PARENT_SLEEP_SECONDS = 30
#: Printed once the grandchild exists, so a caller can tell a live tree from a
#: helper that never started.
READY_MARKER = "spawn-tree-ready"

_CHILD_PROGRAM = (
    f"import sys, time\nfrom pathlib import Path\n\ntime.sleep({CHILD_DELAY_SECONDS})\nPath(sys.argv[1]).touch()\n"
)


def main(argv: list[str]) -> int:
    """Spawn the grandchild and then stay alive until something kills us."""
    if len(argv) != 1:
        print("usage: python -m tests.helpers.spawn_tree <marker>", file=sys.stderr)
        return 2
    marker = str(Path(argv[0]))
    subprocess.Popen([sys.executable, "-c", _CHILD_PROGRAM, marker])
    print(READY_MARKER, flush=True)
    time.sleep(PARENT_SLEEP_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
