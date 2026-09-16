"""The bash tool and the registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from super_agent.runtime.protocol.run_context import LiveContext, RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools import BashTool, DefaultRegistry, NewRegistry, NoTools
from tests.tools.test_workspace import workspace_for


class FakeTool:
    """A tool that records every call it receives, mirroring ``fakeTool``."""

    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec
        self.got: list[ToolCall] = []

    def Specs(self) -> list[ToolSpec]:
        return [self.spec]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        self.got.append(call)
        return "ran " + call.Name


def test_bash_tool_is_risky() -> None:
    for spec in NewRegistry(BashTool()).Specs():
        if spec.Name == "bash":
            assert spec.Risky
            return
    pytest.fail("bash tool not found")


def test_registry_holding_only_the_bash_tool_exposes_only_bash() -> None:
    specs = NewRegistry(BashTool()).Specs()
    assert len(specs) == 1
    assert specs[0].Name == "bash"


@pytest.mark.asyncio
async def test_bash_runs_a_command(tmp_path: Path) -> None:
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await registry.Run(LiveContext(), ToolCall(Name="bash", Input='{"command":"printf hello"}'))

    assert got == "hello"


@pytest.mark.asyncio
async def test_bash_returns_a_failed_command_output_without_an_error(tmp_path: Path) -> None:
    registry = DefaultRegistry(workspace_for(tmp_path))

    got = await registry.Run(LiveContext(), ToolCall(Name="bash", Input='{"command":"printf before; exit 7"}'))

    assert "before" in got
    assert "exit status 7" in got


def test_no_tools_exposes_no_specs() -> None:
    assert NoTools().Specs() == []


def test_registry_aggregates_specs_in_order() -> None:
    registry = NewRegistry(
        FakeTool(ToolSpec(Name="first")),
        FakeTool(ToolSpec(Name="second")),
    )

    specs = registry.Specs()

    assert [spec.Name for spec in specs] == ["first", "second"]


@pytest.mark.asyncio
async def test_registry_dispatches_by_name() -> None:
    first = FakeTool(ToolSpec(Name="first"))
    second = FakeTool(ToolSpec(Name="second"))
    registry = NewRegistry(first, second)

    got = await registry.Run(LiveContext(), ToolCall(Name="second"))

    assert got == "ran second"
    assert first.got == []
    assert [call.Name for call in second.got] == ["second"]


@pytest.mark.asyncio
async def test_registry_rejects_an_unknown_tool() -> None:
    registry = NewRegistry(FakeTool(ToolSpec(Name="known")))

    with pytest.raises(RuntimeError, match="unknown tool: missing"):
        await registry.Run(LiveContext(), ToolCall(Name="missing"))


@pytest.mark.asyncio
async def test_registry_adds_dynamic_tools_atomically_and_removes_them() -> None:
    registry = NewRegistry(FakeTool(ToolSpec(Name="built-in")))
    first = FakeTool(ToolSpec(Name="remote"))
    duplicate = FakeTool(ToolSpec(Name="built-in"))

    with pytest.raises(RuntimeError):
        registry.Add(first, duplicate)
    assert all(spec.Name != "remote" for spec in registry.Specs())

    registry.Add(first)
    got = await registry.Run(LiveContext(), ToolCall(Name="remote"))
    assert got == "ran remote"

    registry.Remove("remote")
    with pytest.raises(RuntimeError):
        await registry.Run(LiveContext(), ToolCall(Name="remote"))
