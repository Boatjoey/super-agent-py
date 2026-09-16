"""Command tools: the runner, its limits, and the workspace commands.

Ported from ``tools/commands.go``. A command's output is capped while it is
read and its process gets its own session, so a command that emits gigabytes or
leaves a background job behind cannot outlive the call or the memory bound.

A non-zero exit is a result, not a failure: :class:`command_exit_error` carries
the status into the output and only the tools that treat it as a diagnosis
swallow it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import signal
import sys
from typing import Any, Final

from super_agent.jsonutil import json_field
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools.env import child_env
from super_agent.tools.files import decode_args, decode_json_object, object_schema
from super_agent.tools.output import capped_buffer
from super_agent.tools.sandbox import command_runner, runner_or_default
from super_agent.tools.workspace import WorkspaceContext, resolve_readable, resolve_writable

if sys.platform == "win32":
    from super_agent.tools.proc_other import (
        spawn_kwargs as spawn_kwargs,
        terminate_process_tree as terminate_process_tree,
    )
else:
    from super_agent.tools.proc_unix import (
        spawn_kwargs as spawn_kwargs,
        terminate_process_tree as terminate_process_tree,
    )

default_command_timeout: Final[float] = 30.0
max_command_timeout: Final[float] = 120.0
default_output_bytes: Final[int] = 20_000
#: Bounds memory the same way ``max_command_timeout`` bounds time. Without it
#: the model can pass an arbitrary limit and buffer a command's entire output.
max_output_bytes: Final[int] = 200_000

#: How much of the merged output stream is read at a time.
_stream_chunk: Final[int] = 64 * 1024

_signal_names: Final[dict[int, str]] = {
    signal.SIGKILL: "killed",
    signal.SIGTERM: "terminated",
    signal.SIGINT: "interrupt",
    signal.SIGQUIT: "quit",
    signal.SIGSEGV: "segmentation fault",
    signal.SIGABRT: "aborted",
    signal.SIGPIPE: "broken pipe",
    signal.SIGHUP: "hangup",
}


class command_exit_error(Exception):
    """A command exited non-zero.

    Its output already reads as a diagnosis for the model, which is why the
    ``bash`` tool returns it rather than reporting a failure.
    """


@dataclasses.dataclass(frozen=True, slots=True)
class _RunCommandArgs:
    Command: str = dataclasses.field(default="", metadata=json_field(name="command"))
    CWD: str = dataclasses.field(default="", metadata=json_field(name="cwd"))
    TimeoutSeconds: int = dataclasses.field(default=0, metadata=json_field(name="timeout_seconds"))
    MaxOutputBytes: int = dataclasses.field(default=0, metadata=json_field(name="max_output_bytes"))
    ContinueOnError: bool = dataclasses.field(default=False, metadata=json_field(name="continue_on_error"))


@dataclasses.dataclass(frozen=True, slots=True)
class _GoTestArgs:
    Packages: list[str] = dataclasses.field(default_factory=list[str], metadata=json_field(name="packages"))
    CWD: str = dataclasses.field(default="", metadata=json_field(name="cwd"))


@dataclasses.dataclass(frozen=True, slots=True)
class _FormatArgs:
    Files: list[str] = dataclasses.field(default_factory=list[str], metadata=json_field(name="files"))


@dataclasses.dataclass(frozen=True, slots=True)
class _GitDiffArgs:
    Paths: list[str] = dataclasses.field(default_factory=list[str], metadata=json_field(name="paths"))


@dataclasses.dataclass(frozen=True, slots=True)
class RunCommandTool:
    """Run a workspace command with cwd, timeout, and output limits."""

    runner: command_runner | None = None
    workspace: WorkspaceContext | None = None

    def Specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                Name="run_command",
                Description="Run a workspace command with cwd, timeout_seconds, and max_output_bytes.",
                Risky=True,
                Parameters=object_schema(
                    {
                        "command": {"type": "string"},
                        "cwd": {"type": "string"},
                        "timeout_seconds": {"type": "integer"},
                        "max_output_bytes": {"type": "integer"},
                        "continue_on_error": {"type": "boolean"},
                    },
                    ["command"],
                ),
            )
        ]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        args = decode_args(call.Input, _RunCommandArgs)
        if args.Command == "":
            raise RuntimeError("command is required")
        cwd = command_cwd(self.workspace, args.CWD)
        output, error = await run_shell(
            runner_or_default(self.runner),
            ctx,
            cwd,
            args.TimeoutSeconds,
            args.MaxOutputBytes,
            args.Command,
        )
        if error is not None and not args.ContinueOnError:
            raise error
        return output


@dataclasses.dataclass(frozen=True, slots=True)
class GoTestTool:
    """Run ``go test`` for workspace packages."""

    runner: command_runner | None = None
    workspace: WorkspaceContext | None = None

    def Specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                Name="go_test",
                Description="Run go test for workspace packages.",
                Risky=True,
                Parameters=object_schema(
                    {
                        "packages": {"type": "array", "items": {"type": "string"}},
                        "cwd": {"type": "string"},
                    },
                    [],
                ),
            )
        ]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        args = _GoTestArgs() if call.Input == "" else decode_args(call.Input, _GoTestArgs)
        packages = args.Packages or ["./..."]
        cwd = command_cwd(self.workspace, args.CWD)
        for package in packages:
            if package.startswith("-"):
                raise RuntimeError("package paths must not start with '-': " + package)
        output, error = await run_exec(
            runner_or_default(self.runner),
            ctx,
            cwd,
            default_command_timeout,
            default_output_bytes,
            "go",
            "test",
            *packages,
        )
        if error is not None:
            raise error
        return output


@dataclasses.dataclass(frozen=True, slots=True)
class FormatTool:
    """Run ``gofmt -w`` on workspace Go files."""

    runner: command_runner | None = None
    workspace: WorkspaceContext | None = None

    def Specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                Name="format",
                Description="Run gofmt -w on workspace Go files.",
                Risky=True,
                Parameters=object_schema(
                    {"files": {"type": "array", "items": {"type": "string"}}},
                    ["files"],
                ),
            )
        ]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        args = decode_args(call.Input, _FormatArgs)
        if not args.Files:
            raise RuntimeError("files is required")
        files = [resolve_writable(self.workspace, name)[0] for name in args.Files]
        cwd = command_cwd(self.workspace, "")
        _, error = await run_exec(
            runner_or_default(self.runner),
            ctx,
            cwd,
            default_command_timeout,
            default_output_bytes,
            "gofmt",
            "-w",
            *files,
        )
        if error is not None:
            raise error
        count = len(files)
        return "formatted " + str(count) + plural(count, " file", " files")


@dataclasses.dataclass(frozen=True, slots=True)
class GitStatusTool:
    """Show the branch and short git status for the workspace."""

    runner: command_runner | None = None
    workspace: WorkspaceContext | None = None

    def Specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                Name="git_status",
                Description="Show the branch and short git status for the workspace.",
                Parameters=object_schema({}, []),
            )
        ]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        if call.Input not in ("", "{}"):
            decode_json_object(call.Input)
        cwd = command_cwd(self.workspace, "")
        output, error = await run_exec(
            runner_or_default(self.runner),
            ctx,
            cwd,
            default_command_timeout,
            default_output_bytes,
            "git",
            "status",
            "--short",
            "--branch",
        )
        if error is not None:
            raise error
        return output


@dataclasses.dataclass(frozen=True, slots=True)
class GitDiffTool:
    """Show ``git diff`` for optional workspace paths."""

    runner: command_runner | None = None
    workspace: WorkspaceContext | None = None

    def Specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                Name="git_diff",
                Description="Show git diff for optional workspace paths.",
                Parameters=object_schema(
                    {"paths": {"type": "array", "items": {"type": "string"}}},
                    [],
                ),
            )
        ]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        args = _GitDiffArgs() if call.Input == "" else decode_args(call.Input, _GitDiffArgs)
        cmd_args = ["diff", "--"]
        for path in args.Paths:
            _, rel = resolve_readable(self.workspace, path)
            cmd_args.append(rel)
        cwd = command_cwd(self.workspace, "")
        output, error = await run_exec(
            runner_or_default(self.runner),
            ctx,
            cwd,
            default_command_timeout,
            default_output_bytes,
            "git",
            *cmd_args,
        )
        if error is not None:
            raise error
        return output


def command_cwd(workspace: WorkspaceContext | None, cwd: str) -> str:
    """Resolve a command tool's cwd inside the workspace.

    A caller that passes none gets the workspace cwd, never the process cwd.
    """
    if workspace is None:
        raise RuntimeError("workspace is not configured")
    if cwd == "":
        cwd = workspace.GetCWD()
    path, _ = resolve_readable(workspace, cwd)
    return path


def command_timeout(seconds: int) -> float:
    """Clamp a model-supplied timeout to the default and the ceiling."""
    if seconds <= 0:
        return default_command_timeout
    timeout = float(seconds)
    if timeout > max_command_timeout:
        return max_command_timeout
    return timeout


def output_limit(max_bytes: int) -> int:
    """Clamp a model-supplied output limit to the default and the ceiling."""
    if max_bytes <= 0:
        return default_output_bytes
    if max_bytes > max_output_bytes:
        return max_output_bytes
    return max_bytes


def plural(count: int, singular: str, plural_form: str) -> str:
    """``singular`` for exactly one, ``plural_form`` otherwise."""
    return singular if count == 1 else plural_form


async def run_shell(
    runner: command_runner,
    ctx: RunContext,
    cwd: str,
    timeout_seconds: int,
    max_bytes: int,
    command: str,
) -> tuple[str, BaseException | None]:
    """Run ``command`` under ``bash -lc`` with the standard limits."""
    return await run_exec(
        runner,
        ctx,
        cwd,
        command_timeout(timeout_seconds),
        output_limit(max_bytes),
        "bash",
        "-lc",
        command,
    )


async def run_exec(
    runner: command_runner,
    ctx: RunContext,
    cwd: str,
    timeout: float,
    max_bytes: int,
    name: str,
    *args: str,
) -> tuple[str, BaseException | None]:
    """Run one subprocess, returning its output and the reason it was cut short.

    The reason is ``None`` on a normal exit and on a non-zero exit (which is
    reported through the output); it is a timeout or cancellation when the
    process was killed.
    """
    args_list = list(args)
    if runner.sandbox is not None:
        workspace_root = ""
        if runner.workspace is not None:
            workspace_root = runner.workspace.GetPrimaryRoot()
        name, args_list, cwd = runner.sandbox.wrap(workspace_root, cwd, name, args_list)
    process = await asyncio.create_subprocess_exec(
        name,
        *args_list,
        cwd=cwd or None,
        env=child_env(),
        # stdout and stderr share one capped reader, so the cap applies to their
        # combined output and neither stream can fill a pipe buffer and block
        # the child.
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **spawn_kwargs,
    )
    sink = capped_buffer(limit=max_bytes)
    reader = asyncio.create_task(_consume(process, sink))
    cancelled = asyncio.create_task(ctx.Done().wait())
    waiting: set[asyncio.Task[Any]] = {reader, cancelled}
    try:
        done, _pending = await asyncio.wait(waiting, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if reader in done:
            await reader
            return _finish(sink, process.returncode)
        reason: BaseException = ctx.Err() or TimeoutError(f"command timed out after {timeout} seconds")
        await terminate_process_tree(process)
        await _settle(reader)
        return sink.String(), reason
    finally:
        await _settle(cancelled)


async def _consume(process: asyncio.subprocess.Process, sink: capped_buffer) -> None:
    """Read the merged output into ``sink`` until the stream closes."""
    stream = process.stdout
    if stream is None:  # pragma: no cover - stdout is always piped here
        raise RuntimeError("command output stream is not available")
    while True:
        chunk = await stream.read(_stream_chunk)
        if not chunk:
            break
        sink.write(chunk)
    await process.wait()


def _finish(sink: capped_buffer, returncode: int | None) -> tuple[str, BaseException | None]:
    """The result of a process that ran to completion."""
    result = sink.String()
    if not returncode:
        return result, None
    error = command_exit_error(_exit_status_message(returncode))
    if result != "" and not result.endswith("\n"):
        result += "\n"
    result += str(error)
    return result, error


def _exit_status_message(returncode: int) -> str:
    """Go's ``exec.ExitError`` text: an exit status, or the killing signal."""
    if returncode < 0:
        return "signal: " + _signal_name(-returncode)
    return f"exit status {returncode}"


def _signal_name(number: int) -> str:
    name = _signal_names.get(number)
    if name is not None:
        return name
    try:
        return signal.Signals(number).name.lower()
    except ValueError:  # pragma: no cover - an unknown signal number
        return str(number)


async def _settle(task: asyncio.Task[Any]) -> None:
    """Reclaim ``task``: cancel it when pending, then wait for it to stop."""
    if task.done():
        # Retrieving the outcome keeps a reader failure from being reported as
        # an unhandled task exception.
        with contextlib.suppress(BaseException):
            task.exception()
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        if not task.cancelled():
            # This coroutine was cancelled, not the child.
            raise
