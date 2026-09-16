"""Execute one scheduled action against the model, the tools, or a human."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from typing import Final, Protocol

from super_agent.errors import Cancelled
from super_agent.runtime.execution.scheduled_action_result import (
    ApprovalReceived,
    ModelReplied,
    ScheduledActionResult,
    ToolFinished,
    ToolQueueChecked,
)
from super_agent.runtime.machine.approval import ApprovalDecision
from super_agent.runtime.machine.scheduled_action import (
    AwaitApproval,
    CallModel,
    CheckToolQueue,
    RunTool,
    ScheduledAction,
)
from super_agent.runtime.permission.types import Request as PermissionRequest
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Message, Model, StreamChunk, ToolCall, ToolRunner, ToolSpec


class ApprovalDismissed(Cancelled):
    """The approval waiter gave up waiting.

    The user dismissed the prompt or the interface went away. The engine treats it
    as a cancellation, not as a fault — the distinction matters because a fault
    would be reported to the model as an error while a cancellation is not.
    """


#: The sentinel the engine compares against with
#: :func:`super_agent.errors.errors_is`. Any other approval failure is a real
#: fault and takes the error path, which answers the outstanding tool calls.
ErrApprovalDismissed: Final[ApprovalDismissed] = ApprovalDismissed("approval dismissed")


class ApprovalWaiter(Protocol):
    """The port the session supplies so a human can decide."""

    async def WaitApproval(self, ctx: RunContext, call: ToolCall, request: PermissionRequest) -> ApprovalDecision:
        """Return the decision, or raise :data:`ErrApprovalDismissed`."""
        ...


class ScheduledActionExecutor(Protocol):
    """Turns a scheduled action into a result."""

    async def Execute(
        self,
        ctx: RunContext,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ScheduledActionResult: ...


@dataclasses.dataclass(frozen=True, slots=True)
class ScheduledActionInput:
    """Everything an action needs that is not the action itself."""

    Messages: tuple[Message, ...] = ()
    ToolSpecs: tuple[ToolSpec, ...] = ()
    ApprovalWaiter: ApprovalWaiter | None = None


class DefaultScheduledActionExecutor:
    """The only executor; the port exists so tests can substitute one."""

    __slots__ = ("_model", "_tools")

    def __init__(self, model: Model | None, tools: ToolRunner | None) -> None:
        self._model = model
        self._tools = tools

    def ToolSpecs(self) -> list[ToolSpec]:
        if self._tools is None:
            return []
        return self._tools.Specs()

    async def Execute(
        self,
        ctx: RunContext,
        action: ScheduledAction,
        env: ScheduledActionInput,
        chunk_func: Callable[[StreamChunk], None],
    ) -> ScheduledActionResult:
        if isinstance(action, CallModel):
            return await self._call_model(ctx, env, chunk_func)
        if isinstance(action, RunTool):
            return await self._run_tool(ctx, action)
        if isinstance(action, CheckToolQueue):
            return ToolQueueChecked()
        if isinstance(action, AwaitApproval):
            return await self._await_approval(ctx, action, env)
        raise ValueError(f"unknown action {type(action).__name__}")

    async def _call_model(
        self, ctx: RunContext, env: ScheduledActionInput, chunk_func: Callable[[StreamChunk], None]
    ) -> ScheduledActionResult:
        if self._model is None:
            raise ValueError("no model is configured")
        response = await self._model.Next(ctx, list(env.Messages), list(env.ToolSpecs), chunk_func)
        return ModelReplied(Response=response)

    async def _run_tool(self, ctx: RunContext, action: RunTool) -> ScheduledActionResult:
        if self._tools is None:
            raise ValueError("no tools are configured")
        try:
            result = await self._tools.Run(ctx, action.Call)
        except Cancelled:
            raise
        except asyncio.CancelledError:
            # A bare CancelledError is a BaseException and would slip past every
            # `except Exception` between here and the engine. Converting it at the
            # adapter boundary keeps the cancel path a normal exception.
            raise Cancelled("tool run cancelled") from None
        except Exception as error:
            # A tool failure is a RESULT, not a fault: it is fed back to the model
            # so it can choose another action. Raising instead would leave the
            # dispatched call unanswered, and a transcript with an unanswered tool
            # call is rejected by the provider — and persisted.
            return ToolFinished(Call=action.Call, Result=f"Error: {error}")
        return ToolFinished(Call=action.Call, Result=result)

    async def _await_approval(
        self, ctx: RunContext, action: AwaitApproval, env: ScheduledActionInput
    ) -> ScheduledActionResult:
        if env.ApprovalWaiter is None:
            raise ValueError("approval waiter is not configured")
        decision = await env.ApprovalWaiter.WaitApproval(ctx, action.Call, action.Request)
        return ApprovalReceived(Call=action.Call, Decision=decision)
