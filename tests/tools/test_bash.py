"""The bash tool and the registry."""

from __future__ import annotations

from pathlib import Path

import pytest

from super_agent.runtime.protocol.run_context import RunContext, live_context
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools import BashTool, NoTools, default_registry, new_registry
from tests.tools.test_workspace import workspace_for


class FakeTool:
    """A tool that records every call it receives, mirroring ``fakeTool``."""

    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec
        self.got: list[ToolCall] = []

    def specs(self) -> list[ToolSpec]:
        return [self.spec]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        self.got.append(call)
        return "ran " + call.name


def test_bash_tool_is_risky() -> None:
    for spec in new_registry(BashTool()).specs():
        if spec.name == "bash":
            assert spec.risky
            return
    pytest.fail("bash tool not found")


def test_registry_holding_only_the_bash_tool_exposes_only_bash() -> None:
    specs = new_registry(BashTool()).specs()
    assert len(specs) == 1
    assert specs[0].name == "bash"


@pytest.mark.asyncio
async def test_bash_runs_a_command(tmp_path: Path) -> None:
    registry = default_registry(workspace_for(tmp_path))

    got = await registry.run(live_context(), ToolCall(name="bash", input='{"command":"printf hello"}'))

    assert got == "hello"


@pytest.mark.asyncio
async def test_bash_returns_a_failed_command_output_without_an_error(tmp_path: Path) -> None:
    registry = default_registry(workspace_for(tmp_path))

    got = await registry.run(live_context(), ToolCall(name="bash", input='{"command":"printf before; exit 7"}'))

    assert "before" in got
    assert "exit status 7" in got


def test_no_tools_exposes_no_specs() -> None:
    assert NoTools().specs() == []


def test_registry_aggregates_specs_in_order() -> None:
    registry = new_registry(
        FakeTool(ToolSpec(name="first")),
        FakeTool(ToolSpec(name="second")),
    )

    specs = registry.specs()

    assert [spec.name for spec in specs] == ["first", "second"]


@pytest.mark.asyncio
async def test_registry_dispatches_by_name() -> None:
    first = FakeTool(ToolSpec(name="first"))
    second = FakeTool(ToolSpec(name="second"))
    registry = new_registry(first, second)

    got = await registry.run(live_context(), ToolCall(name="second"))

    assert got == "ran second"
    assert first.got == []
    assert [call.name for call in second.got] == ["second"]


@pytest.mark.asyncio
async def test_registry_rejects_an_unknown_tool() -> None:
    registry = new_registry(FakeTool(ToolSpec(name="known")))

    with pytest.raises(RuntimeError, match="unknown tool: missing"):
        await registry.run(live_context(), ToolCall(name="missing"))


@pytest.mark.asyncio
async def test_registry_adds_dynamic_tools_atomically_and_removes_them() -> None:
    registry = new_registry(FakeTool(ToolSpec(name="built-in")))
    first = FakeTool(ToolSpec(name="remote"))
    duplicate = FakeTool(ToolSpec(name="built-in"))

    with pytest.raises(RuntimeError):
        registry.add(first, duplicate)
    assert all(spec.name != "remote" for spec in registry.specs())

    registry.add(first)
    got = await registry.run(live_context(), ToolCall(name="remote"))
    assert got == "ran remote"

    registry.remove("remote")
    with pytest.raises(RuntimeError):
        await registry.run(live_context(), ToolCall(name="remote"))
