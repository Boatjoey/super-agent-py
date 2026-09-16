"""Registry lifecycle hooks.

A hook reports a failure by raising. A failing post hook must not turn a
successful tool run into a failed one, because the work has already happened.
"""

from __future__ import annotations

import pytest

from super_agent.runtime.protocol.run_context import RunContext, live_context
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools import new_registry


class ObservedTool:
    """A tool that always succeeds, so only the hooks can fail."""

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(name="observed")]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        return "ok"


@pytest.mark.asyncio
async def test_tool_hooks_run_before_and_after_in_order() -> None:
    registry = new_registry(ObservedTool())
    events: list[str] = []

    def observer(ctx: RunContext, event: str, call: ToolCall, error: BaseException | None) -> None:
        events.append(event)

    registry.set_tool_observer(observer)

    await registry.run(live_context(), ToolCall(name="observed"))

    assert events == ["pre_tool", "post_tool"]


@pytest.mark.asyncio
async def test_post_tool_hook_failure_does_not_clobber_the_tool_result() -> None:
    registry = new_registry(ObservedTool())

    def observer(ctx: RunContext, event: str, call: ToolCall, error: BaseException | None) -> None:
        if event == "post_tool":
            raise RuntimeError("hook exploded")

    registry.set_tool_observer(observer)

    output = await registry.run(live_context(), ToolCall(name="observed"))

    assert "ok" in output
    assert "post_tool hook failed" in output


@pytest.mark.asyncio
async def test_pre_tool_hook_failure_aborts_the_tool() -> None:
    registry = new_registry(ObservedTool())

    def observer(ctx: RunContext, event: str, call: ToolCall, error: BaseException | None) -> None:
        if event == "pre_tool":
            raise RuntimeError("blocked by hook")

    registry.set_tool_observer(observer)

    with pytest.raises(RuntimeError, match="blocked by hook"):
        await registry.run(live_context(), ToolCall(name="observed"))
