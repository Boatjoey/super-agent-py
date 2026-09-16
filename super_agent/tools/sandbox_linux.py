"""Strict command isolation with bubblewrap and prlimit.

Ported from ``tools/sandbox_linux.go``. The argument list is reproduced exactly:
the host root is mounted read-only, the workspace is the only writable host
bind, temporary and home directories are ephemeral, networking follows the
configured policy, and ``prlimit`` bounds CPU time, address space, process
count, and open files.

The type import is guarded because ``sandbox.py`` selects this module at import
time, so importing it back at runtime would be circular.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from super_agent.tools.sandbox import SandboxConfig


@dataclasses.dataclass(slots=True)
class bubblewrap_sandbox:
    """Wraps a command in ``bwrap`` and then ``prlimit``."""

    bwrap: str
    prlimit: str
    config: SandboxConfig

    def wrap(self, workspace_root: str, cwd: str, name: str, args: list[str]) -> tuple[str, list[str], str]:
        """Return the ``bwrap`` command line that contains ``name``."""
        if workspace_root == "":
            raise RuntimeError("sandbox workspace is not configured")
        workspace_root = os.path.realpath(workspace_root, strict=True)
        if cwd == "":
            cwd = workspace_root
        abs_cwd = os.path.realpath(os.path.abspath(cwd), strict=True)
        relative = os.path.relpath(abs_cwd, workspace_root)
        if relative == ".." or relative.startswith(".." + os.sep):
            raise RuntimeError("sandbox cwd is outside workspace")

        sandbox_args = [
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--ro-bind",
            "/",
            "/",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/var/tmp",
            "--bind",
            workspace_root,
            workspace_root,
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--dir",
            "/tmp/super-agent-home",
            "--dir",
            "/tmp/super-agent-cache",
            "--setenv",
            "HOME",
            "/tmp/super-agent-home",
            "--setenv",
            "TMPDIR",
            "/tmp",
            "--setenv",
            "XDG_CACHE_HOME",
            "/tmp/super-agent-cache",
            "--setenv",
            "GOCACHE",
            "/tmp/super-agent-cache/go-build",
            "--chdir",
            abs_cwd,
        ]
        if self.config.AllowNetwork:
            sandbox_args.append("--share-net")
        sandbox_args.extend(
            [
                "--",
                self.prlimit,
                "--cpu=" + str(self.config.CPUSeconds),
                "--as=" + str(self.config.MemoryBytes),
                "--nproc=" + str(self.config.MaxProcesses),
                "--nofile=" + str(self.config.MaxOpenFiles),
                "--",
                name,
                *args,
            ]
        )
        return self.bwrap, sandbox_args, workspace_root


def new_platform_sandbox(config: SandboxConfig) -> bubblewrap_sandbox:
    """Build the bubblewrap sandbox, or refuse when its tools are missing."""
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError("strict sandbox requires bubblewrap: bwrap not found in PATH")
    prlimit = shutil.which("prlimit")
    if prlimit is None:
        raise RuntimeError("strict sandbox requires prlimit: prlimit not found in PATH")
    return bubblewrap_sandbox(bwrap=bwrap, prlimit=prlimit, config=config)
