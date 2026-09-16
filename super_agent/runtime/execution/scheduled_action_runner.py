"""Stamp a queued action's identity onto whatever the executor produced."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Protocol

from super_agent.runtime.execution.run_controller import ActionID, RunID
from super_agent.runtime.execution.scheduled_action_executor import (
    ScheduledActionExecutor,
    ScheduledActionInput,
)
from super_agent.runtime.execution.scheduled_action_result import ScheduledActionResult
from super_agent.runtime.machine.scheduled_action import ScheduledAction
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import StreamChunk, ToolSpec


@dataclasses.dataclass(frozen=True, slots=True)
class QueuedAction:
    """A scheduled action plus the run and action it belongs to."""

    run_id: RunID = dataclasses.field(default_factory=lambda: RunID(""))
    action_id: ActionID = dataclasses.field(default_factory=lambda: ActionID(""))
    action: ScheduledAction | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ActionCompletion:
    """A queued action's result, carrying the identity it was stamped with."""

    run_id: RunID = dataclasses.field(default_factory=lambda: RunID(""))
    action_id: ActionID = dataclasses.field(default_factory=lambda: ActionID(""))
    result: ScheduledActionResult | None = None


class ScheduledActionRunner(Protocol):
    """Runs one queued action and reports which action finished."""

    async def run(
        self,
        ctx: RunContext,
        action: QueuedAction,
        input: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ActionCompletion: ...

    def tool_specs(self) -> list[ToolSpec]: ...


class DefaultScheduledActionRunner:
    """The only runner; the port exists so tests can substitute one."""

    __slots__ = ("_executor",)

    def __init__(self, executor: ScheduledActionExecutor) -> None:
        self._executor = executor

    def tool_specs(self) -> list[ToolSpec]:
        """The specs the executor advertises, if it advertises any."""
        provider = getattr(self._executor, "tool_specs", None)
        if provider is None:
            return []
        specs: list[ToolSpec] = provider()
        return specs

    async def run(
        self,
        ctx: RunContext,
        action: QueuedAction,
        input: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ActionCompletion:
        if action.action is None:
            raise ValueError("queued action carries no action")
        result = await self._executor.execute(ctx, action.action, input, chunk_func)
        return ActionCompletion(run_id=action.run_id, action_id=action.action_id, result=result)
