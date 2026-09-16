"""The tool runner used when tools are switched off.

It advertises nothing and refuses every call, which is what the engine needs to
keep a run alive without a tool set.
"""

from __future__ import annotations

import dataclasses

from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec


@dataclasses.dataclass(frozen=True, slots=True)
class NoTools:
    """A tool runner with no tools."""

    def specs(self) -> list[ToolSpec]:
        return []

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        raise RuntimeError("tools are disabled")
