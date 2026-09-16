"""Extension workflows: hooks, custom commands, and the read-only repository queries.

A hook runs a shell command through ``tools.Registry.RunDirect``, never through
``Run``: ``Run`` is what triggers the pre-tool and post-tool hooks, so a hook that
reached it would re-enter the observer that invoked it.
"""

from __future__ import annotations

from typing import Protocol

from super_agent import jsonutil
from super_agent.app.extensions import Extensions
from super_agent.errors import JoinedError
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec

__all__ = ["Runner", "WorkflowController"]

#: The empty JSON object used when a call takes no arguments.
NO_ARGUMENTS = "{}"


class Runner(Protocol):
    """The tool runner the workflow queries reach.

    Declared here rather than imported so this module does not depend on the
    composition code that wraps the registry with the hooks.
    """

    def Specs(self) -> list[ToolSpec]: ...

    async def Run(self, ctx: RunContext, call: ToolCall) -> str: ...

    async def RunDirect(self, ctx: RunContext, call: ToolCall) -> str: ...


class WorkflowController:
    """Hooks, custom commands, git queries, and language-server diagnostics."""

    __slots__ = ("extensions", "registry")

    def __init__(self, registry: Runner | None, extensions: Extensions) -> None:
        self.registry = registry
        self.extensions = extensions

    async def GitDiff(self, ctx: RunContext) -> str:
        """The working tree's diff."""
        if self.registry is None:
            raise ValueError("workflow tools are unavailable")
        return await self.registry.Run(ctx, ToolCall(Name="git_diff", Input=NO_ARGUMENTS))

    async def GitStatus(self, ctx: RunContext) -> str:
        """The working tree's short status."""
        if self.registry is None:
            raise ValueError("workflow tools are unavailable")
        return await self.registry.Run(ctx, ToolCall(Name="git_status", Input=NO_ARGUMENTS))

    async def Diagnostics(self, ctx: RunContext, path: str) -> str:
        """The language server's diagnostics for one file."""
        if self.registry is None:
            raise ValueError("workflow tools are unavailable")
        return await self.registry.Run(ctx, ToolCall(Name="lsp_diagnostics", Input=jsonutil.dumps({"path": path})))

    async def RunHook(self, ctx: RunContext, event: str) -> None:
        """Run every command configured for ``event``, in order."""
        for command in self.extensions.Hooks.get(event, ()):
            if self.registry is None:
                raise ValueError("hooks require tools")
            payload = jsonutil.dumps({"command": command})
            await self.registry.RunDirect(ctx, ToolCall(Name="run_command", Input=payload))

    async def RunHooks(self, ctx: RunContext, *events: str) -> None:
        """Run several events' hooks, joining every failure into one error.

        A failure never stops the next event: the hooks are independent, and the
        caller wants to know about all of them.
        """
        failures: list[BaseException] = []
        for event in events:
            try:
                await self.RunHook(ctx, event)
            except Exception as error:
                failures.append(error)
        if failures:
            raise JoinedError(*failures)

    def CustomCommands(self) -> list[str]:
        """Every custom command name, sorted."""
        return sorted(self.extensions.Commands)

    def Skills(self) -> list[str]:
        """Every discovered skill name."""
        return list(self.extensions.Skills)

    def Plugins(self) -> list[str]:
        """Every discovered plugin name."""
        return list(self.extensions.Plugins)

    def ExpandCommand(self, name: str, arguments: str) -> str:
        """Substitute ``$ARGUMENTS``, or append the arguments when there is none."""
        template = self.extensions.Commands.get(name)
        if template is None:
            raise ValueError("unknown custom command: " + name)
        if "$ARGUMENTS" in template:
            return template.replace("$ARGUMENTS", arguments)
        if arguments.strip() != "":
            template += "\n\nArguments: " + arguments
        return template
