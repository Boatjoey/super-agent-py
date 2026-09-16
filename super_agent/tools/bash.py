"""The ``bash`` tool.

It runs through the same runner as every other command tool, so it inherits the
command timeout, output cap, process group, and environment scrubbing rather than
reimplementing them.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any, cast

from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools.commands import (
    command_cwd,
    command_exit_error,
    default_command_timeout,
    default_output_bytes,
    run_exec,
)
from super_agent.tools.sandbox import command_runner, runner_or_default
from super_agent.tools.workspace import WorkspaceContext


@dataclasses.dataclass(frozen=True, slots=True)
class BashTool:
    """Run a bash command after user approval."""

    runner: command_runner | None = None
    workspace: WorkspaceContext | None = None

    def Specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                Name="bash",
                Description="Run a bash command after user approval.",
                Risky=True,
                Parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
            )
        ]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        command = bash_command(call.Input)
        if command == "":
            raise RuntimeError("invalid bash command input: must be JSON with 'command' field")
        cwd = command_cwd(self.workspace, "")
        output, error = await run_exec(
            runner_or_default(self.runner),
            ctx,
            cwd,
            default_command_timeout,
            default_output_bytes,
            "bash",
            "-lc",
            command,
        )
        if error is None:
            return output
        ctx.RaiseIfCancelled()
        if isinstance(error, command_exit_error):
            # A non-zero exit status is a normal result rather than a tool
            # failure: the output and status already read as a diagnosis for the
            # model.
            return output
        raise error


def bash_command(text: str) -> str:
    """The ``command`` field of ``text``, or ``""`` when it is not there."""
    try:
        data: object = json.loads(text)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    command = cast("dict[str, Any]", data).get("command")
    return command if isinstance(command, str) else ""
