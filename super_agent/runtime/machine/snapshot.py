"""Validation and the read-only view a transition guards against.

``validate_runtime_data`` is the authority on what a coherent ``RuntimeData`` looks
like. ``snapshot_from`` runs it before every transition, and the applier runs it
again on the cloned candidate, so an invalid intermediate state can never be
committed.
"""

from __future__ import annotations

import dataclasses

from super_agent.runtime.machine.errors import InvariantViolationError
from super_agent.runtime.machine.runtime_data import RuntimeData
from super_agent.runtime.machine.state import (
    STATE_ADVANCING_QUEUE,
    STATE_IDLE,
    STATE_INITIALIZING,
    STATE_RUNNING_TOOL,
    STATE_WAITING_APPROVAL,
    STATE_WAITING_LLM,
    State,
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


def snapshot_from(runtime_data: RuntimeData) -> MachineSnapshot:
    """Validate ``runtime_data`` and build the snapshot a transition reads."""
    validate_runtime_data(runtime_data)

    view = QueueView(has_batch=runtime_data.tool_batch is not None)
    batch = runtime_data.tool_batch
    if batch is not None and batch.index < len(batch.calls):
        view = QueueView(
            has_batch=True,
            next=batch.calls[batch.index],
            remaining=tuple(batch.calls[batch.index :]),
        )
    return MachineSnapshot(
        state=runtime_data.state,
        pending_tool=runtime_data.pending_tool,
        current_tool=runtime_data.current_tool,
        queue=view,
    )


def validate_runtime_data(runtime_data: RuntimeData) -> None:
    """Raise :class:`InvariantViolationError` unless the data is coherent."""
    batch = runtime_data.tool_batch
    if batch is not None and (batch.index < 0 or batch.index > len(batch.calls)):
        raise InvariantViolationError("tool batch index is out of range")
    if runtime_data.pending_tool is None and runtime_data.pending_permission is not None:
        raise InvariantViolationError("pending permission has no pending tool")
    if (runtime_data.streaming_content != "" or runtime_data.streaming_reasoning != "") and (
        runtime_data.state != STATE_WAITING_LLM
    ):
        raise InvariantViolationError("streaming content exists outside WaitingLLM")

    state = runtime_data.state
    if state in (STATE_INITIALIZING, STATE_IDLE, STATE_WAITING_LLM):
        if (
            runtime_data.pending_tool is not None
            or runtime_data.pending_permission is not None
            or runtime_data.current_tool is not None
            or runtime_data.tool_batch is not None
        ):
            raise InvariantViolationError(f"{state} contains tool execution context")
        return
    if state == STATE_ADVANCING_QUEUE:
        if runtime_data.tool_batch is None:
            raise InvariantViolationError("AdvancingQueue has no tool batch")
        if (
            runtime_data.pending_tool is not None
            or runtime_data.pending_permission is not None
            or runtime_data.current_tool is not None
        ):
            raise InvariantViolationError("AdvancingQueue contains a pending or current tool")
        return
    if state == STATE_WAITING_APPROVAL:
        if (
            runtime_data.tool_batch is None
            or runtime_data.pending_tool is None
            or runtime_data.pending_permission is None
        ):
            raise InvariantViolationError("WaitingApproval requires a batch, pending tool, and permission")
        if runtime_data.current_tool is not None:
            raise InvariantViolationError("WaitingApproval contains a current tool")
        if not _batch_previous_call_matches(runtime_data.tool_batch, runtime_data.pending_tool):
            raise InvariantViolationError("pending tool does not match the advanced batch call")
        return
    if state == STATE_RUNNING_TOOL:
        if runtime_data.tool_batch is None or runtime_data.current_tool is None:
            raise InvariantViolationError("RunningTool requires a batch and current tool")
        if runtime_data.pending_tool is not None or runtime_data.pending_permission is not None:
            raise InvariantViolationError("RunningTool contains pending approval context")
        if not _batch_previous_call_matches(runtime_data.tool_batch, runtime_data.current_tool):
            raise InvariantViolationError("current tool does not match the advanced batch call")
        return
    raise InvariantViolationError(f"unknown state {state}")


def _batch_previous_call_matches(batch: ToolCallBatch | None, call: ToolCall | None) -> bool:
    """True when ``call`` is the batch entry the index has just consumed."""
    return (
        batch is not None
        and call is not None
        and batch.index > 0
        and batch.index <= len(batch.calls)
        and same_tool_call(batch.calls[batch.index - 1], call)
    )


def same_tool_call(left: ToolCall, right: ToolCall) -> bool:
    """Compare two calls the way the machine must: by ID when either has one.

    Falling back to name and input keeps a replayed call with no ID usable, and
    comparing by ID first means an adapter that rewrites the input cannot make a
    stale result look current.
    """
    if left.id != "" or right.id != "":
        return left.id != "" and left.id == right.id
    return left.name == right.name and left.input == right.input
