"""Sandbox configuration and the command runner that consumes it.

The platform-specific construction lives in ``sandbox_linux.py`` and
``sandbox_other.py``; ``sys.platform`` selects between them the way platform
dispatch does.

Strict mode fails closed on purpose: an unsupported platform must refuse to run
a command rather than silently running it unsandboxed.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from typing import Final, Protocol

from super_agent.tools.workspace import WorkspaceContext

if sys.platform == "linux":
    from super_agent.tools.sandbox_linux import new_platform_sandbox as new_platform_sandbox
else:
    from super_agent.tools.sandbox_other import new_platform_sandbox as new_platform_sandbox


class SandboxMode(str):
    """How commands run. A ``str`` subclass so it serialises as its value."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"SandboxMode({str.__repr__(self)})"


SANDBOX_MODE_OFF: Final[SandboxMode] = SandboxMode("off")
SANDBOX_MODE_STRICT: Final[SandboxMode] = SandboxMode("strict")

#: The zero value for the type; an empty mode means strict, not off.
ZERO_SANDBOX_MODE: Final[SandboxMode] = SandboxMode("")


@dataclasses.dataclass(slots=True)
class SandboxConfig:
    """Parameters for command containment, sourced from top-level settings."""

    mode: SandboxMode = ZERO_SANDBOX_MODE
    workspace: str = ""
    allow_network: bool = False
    cpu_seconds: int = 0
    memory_bytes: int = 0
    max_processes: int = 0
    max_open_files: int = 0


def default_sandbox_config(workspace: str) -> SandboxConfig:
    """The strict defaults: one workspace, no network, bounded resources."""
    return SandboxConfig(
        mode=SANDBOX_MODE_STRICT,
        workspace=workspace,
        cpu_seconds=120,
        memory_bytes=1 << 30,
        max_processes=128,
        max_open_files=256,
    )


def valid_sandbox_mode(mode: SandboxMode) -> bool:
    """Whether ``mode`` names a mode this build understands."""
    return mode in (SANDBOX_MODE_OFF, SANDBOX_MODE_STRICT)


class command_sandbox(Protocol):
    """Rewrites a command so it runs contained."""

    def wrap(self, workspace_root: str, cwd: str, name: str, args: list[str]) -> tuple[str, list[str], str]:
        """Return the ``(executable, args, cwd)`` that actually runs."""
        ...


@dataclasses.dataclass(slots=True)
class command_runner:
    """A command sandbox plus the workspace binding it resolves against.

    ``sandbox`` is ``None`` when commands run unsandboxed, which is what
    ``sandbox.mode: off`` selects.
    """

    sandbox: command_sandbox | None = None
    workspace: WorkspaceContext | None = None


#: Falls back to the unsandboxed direct runner. Production registries are built
#: through ``sandboxed_registry``, which always supplies a runner; the fallback
#: exists for tests that construct bare tool values.
direct_command_runner: Final[command_runner] = command_runner()


def runner_or_default(runner: command_runner | None) -> command_runner:
    """``runner`` when supplied, the unsandboxed runner otherwise."""
    return direct_command_runner if runner is None else runner


def new_command_runner(config: SandboxConfig, workspace_context: WorkspaceContext | None) -> command_runner:
    """Build the runner for ``config``, refusing a configuration it cannot honour."""
    if config.mode == "":
        config.mode = SANDBOX_MODE_STRICT
    if not valid_sandbox_mode(config.mode):
        raise RuntimeError("invalid sandbox mode: " + str(config.mode))
    if config.mode == SANDBOX_MODE_OFF:
        return command_runner(workspace=workspace_context)
    if config.workspace == "":
        raise RuntimeError("strict sandbox requires a workspace")
    config.workspace = os.path.realpath(os.path.abspath(config.workspace), strict=True)
    if config.cpu_seconds <= 0:
        # The command limits live in ``commands.py``. The import stays local to
        # keep ``sandbox`` and ``commands`` acyclic.
        from super_agent.tools.commands import max_command_timeout

        config.cpu_seconds = int(max_command_timeout)
    if config.memory_bytes <= 0:
        config.memory_bytes = 1 << 30
    if config.max_processes <= 0:
        config.max_processes = 128
    if config.max_open_files <= 0:
        config.max_open_files = 256
    return command_runner(sandbox=new_platform_sandbox(config), workspace=workspace_context)
