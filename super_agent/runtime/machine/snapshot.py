"""Validation and the read-only view a transition guards against.

``ValidateRuntimeData`` is the authority on what a coherent ``RuntimeData`` looks
like. ``SnapshotFrom`` runs it before every transition, and the applier runs it
again on the cloned candidate, so an invalid intermediate state can never be
committed.
"""

from __future__ import annotations

import dataclasses

from super_agent.runtime.machine.errors import InvariantViolationError
from super_agent.runtime.machine.runtime_data import RuntimeData
from super_agent.runtime.machine.state import (
    State,
    StateAdvancingQueue,
    StateIdle,
    StateInitializing,
    StateRunningTool,
    StateWaitingApproval,
    StateWaitingLLM,
)
from super_agent.runtime.machine.tool_batch import ToolCallBatch
from super_agent.runtime.protocol.types import ToolCall


@dataclasses.dataclass(frozen=True, slots=True)
class QueueView:
    """What the batch still owes the loop."""

    has_batch: bool = False
    next: ToolCall | None = None
    #: The batch calls from the current index onward, i.e. everything not yet
    #: dispatched. Error handling needs the whole list to answer every tool call
    #: the model asked for.
    remaining: tuple[ToolCall, ...] = ()

    def empty(self) -> bool:
        return self.has_batch and self.next is None


@dataclasses.dataclass(frozen=True, slots=True)
class MachineSnapshot:
    """A validated read-only view exposing only what transitions guard on."""

    state: State
    pending_tool: ToolCall | None = None
    current_tool: ToolCall | None = None
    queue: QueueView = dataclasses.field(default_factory=QueueView)


def SnapshotFrom(runtime_data: RuntimeData) -> MachineSnapshot:
    """Validate ``runtime_data`` and build the snapshot a transition reads."""
    ValidateRuntimeData(runtime_data)

    view = QueueView(has_batch=runtime_data.ToolBatch is not None)
    batch = runtime_data.ToolBatch
    if batch is not None and batch.Index < len(batch.Calls):
        view = QueueView(
            has_batch=True,
            next=batch.Calls[batch.Index],
            remaining=tuple(batch.Calls[batch.Index :]),
        )
    return MachineSnapshot(
        state=runtime_data.State,
        pending_tool=runtime_data.PendingTool,
        current_tool=runtime_data.CurrentTool,
        queue=view,
    )


def ValidateRuntimeData(runtime_data: RuntimeData) -> None:
    """Raise :class:`InvariantViolationError` unless the data is coherent."""
    batch = runtime_data.ToolBatch
    if batch is not None and (batch.Index < 0 or batch.Index > len(batch.Calls)):
        raise InvariantViolationError("tool batch index is out of range")
    if runtime_data.PendingTool is None and runtime_data.PendingPermission is not None:
        raise InvariantViolationError("pending permission has no pending tool")
    if (runtime_data.StreamingContent != "" or runtime_data.StreamingReasoning != "") and (
        runtime_data.State != StateWaitingLLM
    ):
        raise InvariantViolationError("streaming content exists outside WaitingLLM")

    state = runtime_data.State
    if state in (StateInitializing, StateIdle, StateWaitingLLM):
        if (
            runtime_data.PendingTool is not None
            or runtime_data.PendingPermission is not None
            or runtime_data.CurrentTool is not None
            or runtime_data.ToolBatch is not None
        ):
            raise InvariantViolationError(f"{state} contains tool execution context")
        return
    if state == StateAdvancingQueue:
        if runtime_data.ToolBatch is None:
            raise InvariantViolationError("AdvancingQueue has no tool batch")
        if (
            runtime_data.PendingTool is not None
            or runtime_data.PendingPermission is not None
            or runtime_data.CurrentTool is not None
        ):
            raise InvariantViolationError("AdvancingQueue contains a pending or current tool")
        return
    if state == StateWaitingApproval:
        if runtime_data.ToolBatch is None or runtime_data.PendingTool is None or runtime_data.PendingPermission is None:
            raise InvariantViolationError("WaitingApproval requires a batch, pending tool, and permission")
        if runtime_data.CurrentTool is not None:
            raise InvariantViolationError("WaitingApproval contains a current tool")
        if not _batch_previous_call_matches(runtime_data.ToolBatch, runtime_data.PendingTool):
            raise InvariantViolationError("pending tool does not match the advanced batch call")
        return
    if state == StateRunningTool:
        if runtime_data.ToolBatch is None or runtime_data.CurrentTool is None:
            raise InvariantViolationError("RunningTool requires a batch and current tool")
        if runtime_data.PendingTool is not None or runtime_data.PendingPermission is not None:
            raise InvariantViolationError("RunningTool contains pending approval context")
        if not _batch_previous_call_matches(runtime_data.ToolBatch, runtime_data.CurrentTool):
            raise InvariantViolationError("current tool does not match the advanced batch call")
        return
    raise InvariantViolationError(f"unknown state {state}")


def _batch_previous_call_matches(batch: ToolCallBatch | None, call: ToolCall | None) -> bool:
    """True when ``call`` is the batch entry the index has just consumed."""
    return (
        batch is not None
        and call is not None
        and batch.Index > 0
        and batch.Index <= len(batch.Calls)
        and same_tool_call(batch.Calls[batch.Index - 1], call)
    )


def same_tool_call(left: ToolCall, right: ToolCall) -> bool:
    """Compare two calls the way the machine must: by ID when either has one.

    Falling back to name and input keeps a replayed call with no ID usable, and
    comparing by ID first means an adapter that rewrites the input cannot make a
    stale result look current.
    """
    if left.ID != "" or right.ID != "":
        return left.ID != "" and left.ID == right.ID
    return left.Name == right.Name and left.Input == right.Input
