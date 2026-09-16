"""Port of ``tests/tools/hooks_test.go``: registry lifecycle hooks.

A hook reports a failure by raising, which is the Python form of Go's
``return err``. A failing post hook must not turn a successful tool run into a
failed one, because the work has already happened.
"""

from __future__ import annotations

import pytest

from super_agent.runtime.protocol.run_context import LiveContext, RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools import NewRegistry


class ObservedTool:
    """A tool that always succeeds, so only the hooks can fail."""

    def Specs(self) -> list[ToolSpec]:
        return [ToolSpec(Name="observed")]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        return "ok"


@pytest.mark.asyncio
async def test_tool_hooks_run_before_and_after_in_order() -> None:
    registry = NewRegistry(ObservedTool())
    events: list[str] = []

    def observer(ctx: RunContext, event: str, call: ToolCall, error: BaseException | None) -> None:
        events.append(event)

    registry.SetToolObserver(observer)

    await registry.Run(LiveContext(), ToolCall(Name="observed"))

    assert events == ["pre_tool", "post_tool"]


@pytest.mark.asyncio
async def test_post_tool_hook_failure_does_not_clobber_the_tool_result() -> None:
    registry = NewRegistry(ObservedTool())

    def observer(ctx: RunContext, event: str, call: ToolCall, error: BaseException | None) -> None:
        if event == "post_tool":
            raise RuntimeError("hook exploded")

    registry.SetToolObserver(observer)

    output = await registry.Run(LiveContext(), ToolCall(Name="observed"))

    assert "ok" in output
    assert "post_tool hook failed" in output


@pytest.mark.asyncio
async def test_pre_tool_hook_failure_aborts_the_tool() -> None:
    registry = NewRegistry(ObservedTool())

    def observer(ctx: RunContext, event: str, call: ToolCall, error: BaseException | None) -> None:
        if event == "pre_tool":
            raise RuntimeError("blocked by hook")

    registry.SetToolObserver(observer)

    with pytest.raises(RuntimeError, match="blocked by hook"):
        await registry.Run(LiveContext(), ToolCall(Name="observed"))
