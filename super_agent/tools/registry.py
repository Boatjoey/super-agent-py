"""The tool registry: advertisement, dispatch, and lifecycle hooks.

No lock guards the registry: every mutation here is synchronous and the event loop
cannot interleave it, so ``add``, ``remove``, and ``replace`` are atomic by
construction and a lock would only be ceremony.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools.bash import BashTool
from super_agent.tools.commands import GitDiffTool, GitStatusTool, RunCommandTool
from super_agent.tools.files import ApplyPatchTool, ListFilesTool, ReadFileTool, SearchTool, WriteFileTool
from super_agent.tools.sandbox import SandboxConfig, command_runner, new_command_runner
from super_agent.tools.web import BrowserFetchTool, WebSearchTool
from super_agent.tools.workspace import WorkspaceContext


class Tool(Protocol):
    """One tool: what it advertises and how it runs."""

    def specs(self) -> list[ToolSpec]:
        """Every spec this tool exposes."""
        ...

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        """Execute ``call``, raising on failure."""
        ...


#: A lifecycle hook. It reports a failure by raising.
type ToolObserver = Callable[[RunContext, str, ToolCall, BaseException | None], None]

#: A checkpoint hook run before a risky tool. It raises to refuse the call.
type CheckpointCallback = Callable[[ToolCall], None]


class Registry:
    """The set of tools the model can see."""

    def __init__(self, *items: Tool) -> None:
        self._tools: dict[str, Tool] = {}
        self._order: list[str] = []
        self._checkpoint: CheckpointCallback | None = None
        self._observer: ToolObserver | None = None
        for item in items:
            name = _tool_name(item)
            self._order.append(name)
            self._tools[name] = item

    def set_tool_observer(self, observer: ToolObserver | None) -> None:
        """Install the pre/post hook, or clear it."""
        self._observer = observer

    def set_checkpoint_callback(self, callback: CheckpointCallback | None) -> None:
        """Install the risky-tool checkpoint, or clear it."""
        self._checkpoint = callback

    def add(self, *items: Tool | None) -> None:
        """Atomically add dynamically discovered tools.

        No tool is added when a name is empty, duplicated in the batch, or
        already registered.
        """
        self._add(items)

    def _add(self, items: Sequence[Tool | None]) -> None:
        seen: set[str] = set()
        for item in items:
            if item is None:
                raise RuntimeError("cannot register nil tool")
            name = _tool_name(item)
            if name == "":
                raise RuntimeError("cannot register tool with empty name")
            if name in self._tools:
                raise RuntimeError(f"tool {name!r} is already registered")
            if name in seen:
                raise RuntimeError(f"tool {name!r} is duplicated")
            seen.add(name)
        for item in items:
            if item is None:  # pragma: no cover - rejected above
                continue
            name = _tool_name(item)
            self._order.append(name)
            self._tools[name] = item

    def remove(self, *names: str) -> None:
        """Forget every named tool."""
        remove = set(names)
        for name in remove:
            self._tools.pop(name, None)
        self._order = [name for name in self._order if name not in remove]

    def replace(self, remove_names: Sequence[str], *items: Tool | None) -> None:
        """Atomically remove old names and register a replacement batch."""
        remove = set(remove_names)
        existing = {name for name in self._tools if name not in remove}
        seen: set[str] = set()
        for item in items:
            if item is None or _tool_name(item) == "":
                raise RuntimeError("replacement contains invalid tool")
            name = _tool_name(item)
            if name in existing:
                raise RuntimeError(f"tool {name!r} is already registered")
            if name in seen:
                raise RuntimeError(f"tool {name!r} is duplicated")
            seen.add(name)
        order: list[str] = []
        for name in self._order:
            if name in remove:
                self._tools.pop(name, None)
            else:
                order.append(name)
        for item in items:
            if item is None:  # pragma: no cover - rejected above
                continue
            name = _tool_name(item)
            order.append(name)
            self._tools[name] = item
        self._order = order

    def specs(self) -> list[ToolSpec]:
        """Every visible tool's specs, in registration order."""
        specs: list[ToolSpec] = []
        for name in self._order:
            specs.extend(self._tools[name].specs())
        return specs

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        """Run ``call`` through the lifecycle hooks."""
        observer = self._observer
        if observer is None:
            return await self.run_direct(ctx, call)
        observer(ctx, "pre_tool", call, None)
        result = ""
        error: BaseException | None = None
        try:
            result = await self.run_direct(ctx, call)
        except BaseException as exc:
            error = exc
        try:
            observer(ctx, "post_tool", call, error)
        except BaseException as hook_error:
            # A post-hook failure must not turn the tool run into an error: the
            # work already happened, and an error result would make the engine
            # report the call as failed. Surface it in the output; when the tool
            # itself failed, its own error dominates.
            if error is None:
                result += "\n[post_tool hook failed: " + str(hook_error) + "]"
        if error is not None:
            raise error
        return result

    async def run_direct(self, ctx: RunContext, call: ToolCall) -> str:
        """Run ``call`` without hooks; hooks use this to avoid recursing."""
        tool = self._tools.get(call.name)
        if tool is None:
            raise RuntimeError("unknown tool: " + call.name)
        if self._checkpoint is not None and _is_risky(tool, call.name):
            self._checkpoint(call)
        return await tool.run(ctx, call)


def new_registry(*items: Tool) -> Registry:
    """Build a registry over ``items``, in order."""
    return Registry(*items)


def default_registry(workspace: WorkspaceContext | None) -> Registry:
    """Every built-in tool, with commands run directly."""
    return registry_with_runner(None, workspace)


def registry_for_workspace(workspace: WorkspaceContext | None) -> Registry:
    """``default_registry`` for one workspace binding."""
    return default_registry(workspace)


def sandboxed_registry(config: SandboxConfig, workspace: WorkspaceContext | None) -> Registry:
    """Every built-in tool, with commands run under ``config``."""
    if workspace is None:
        raise RuntimeError("workspace is not configured")
    config.workspace = workspace.get_primary_root()
    runner = new_command_runner(config, workspace)
    return registry_with_runner(runner, workspace)


def registry_with_runner(runner: command_runner | None, workspace: WorkspaceContext | None) -> Registry:
    """The built-in set, wired to ``runner``."""
    return new_registry(
        ReadFileTool(workspace=workspace),
        ListFilesTool(workspace=workspace),
        SearchTool(workspace=workspace),
        ApplyPatchTool(workspace=workspace),
        WriteFileTool(workspace=workspace),
        RunCommandTool(runner=runner, workspace=workspace),
        GitStatusTool(runner=runner, workspace=workspace),
        GitDiffTool(runner=runner, workspace=workspace),
        BashTool(runner=runner, workspace=workspace),
        WebSearchTool(),
        BrowserFetchTool(),
    )


def _tool_name(item: Tool) -> str:
    """The name a tool is registered under."""
    specs = item.specs()
    if not specs:
        raise RuntimeError("tool exposes no spec")
    return specs[0].name


def _is_risky(tool: Tool, name: str) -> bool:
    """Whether the spec ``name`` selects is a risky one."""
    return any(spec.risky for spec in tool.specs() if spec.name == name)
