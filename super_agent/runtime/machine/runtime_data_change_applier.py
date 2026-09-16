"""Apply runtime-data changes to a clone, then validate the clone.

The clone/apply/validate sequence is the transaction the whole engine rests on:
a candidate that fails validation is discarded and nothing is committed. The
clone is written out field by field rather than with
:func:`dataclasses.replace`, because ``replace`` copies shallowly and would alias
the list it is supposed to protect, and rather than :func:`copy.deepcopy`,
because that would hide which fields are shared by value and which by reference.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from super_agent.runtime.machine.transition import TransitionResult

from super_agent.runtime.machine.errors import InvariantViolationError
from super_agent.runtime.machine.runtime_data import RuntimeData
from super_agent.runtime.machine.runtime_data_change import (
    AdvanceToolCallBatch,
    AppendAssistantMessage,
    AppendStreamingAssistant,
    AppendToolResult,
    AppendUserMessage,
    ClearCurrentTool,
    ClearPendingTool,
    ClearToolCallBatch,
    FlushStreamingAssistant,
    ResetConversation,
    RuntimeDataChange,
    SetCurrentTool,
    SetPendingTool,
    SetToolCallBatch,
)
from super_agent.runtime.machine.snapshot import validate_runtime_data
from super_agent.runtime.machine.tool_batch import ToolCallBatch
from super_agent.runtime.permission.types import Request as PermissionRequest
from super_agent.runtime.protocol.types import (
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ROLE_TOOL,
    ROLE_USER,
    Message,
    ToolCall,
)


@dataclasses.dataclass(frozen=True, slots=True)
class RuntimeDataChangeResult:
    """The validated runtime data a transition should commit."""

    runtime_data: RuntimeData


class RuntimeDataChangeApplier(Protocol):
    """The engine's port for turning a decision into committed data."""

    def apply_runtime_data_changes(
        self, runtime_data: RuntimeData, result: TransitionResult
    ) -> RuntimeDataChangeResult: ...


class DefaultRuntimeDataChangeApplier:
    """The only applier; the port exists so tests can substitute one."""

    def apply_runtime_data_changes(
        self, runtime_data: RuntimeData, result: TransitionResult
    ) -> RuntimeDataChangeResult:
        """Clone ``runtime_data``, apply the transition's changes, and validate."""
        next_data = clone_runtime_data(runtime_data)
        next_data.state = result.next_state
        for change in result.runtime_data_changes:
            _apply_runtime_data_change(next_data, change)
        validate_runtime_data(next_data)
        return RuntimeDataChangeResult(runtime_data=next_data)


def _apply_runtime_data_change(data: RuntimeData, change: RuntimeDataChange) -> None:
    if isinstance(change, AppendUserMessage):
        data.streaming_content = ""
        data.streaming_reasoning = ""
        data.messages.append(Message(role=ROLE_USER, content=change.content, attachments=change.attachments))
    elif isinstance(change, AppendAssistantMessage):
        data.streaming_content = ""
        data.streaming_reasoning = ""
        data.messages.append(clone_message(change.message))
    elif isinstance(change, AppendToolResult):
        data.streaming_content = ""
        data.streaming_reasoning = ""
        data.messages.append(
            Message(
                role=ROLE_TOOL,
                content=change.result,
                tool_call_id=change.call.id,
                tool_name=change.call.name,
            )
        )
    elif isinstance(change, AppendStreamingAssistant):
        data.streaming_content += change.chunk.content_delta
        data.streaming_reasoning += change.chunk.reasoning_content_delta
    elif isinstance(change, FlushStreamingAssistant):
        if data.streaming_content != "" or data.streaming_reasoning != "":
            data.messages.append(
                Message(
                    role=ROLE_ASSISTANT,
                    content=data.streaming_content,
                    reasoning_content=data.streaming_reasoning,
                    interrupted=change.interrupted,
                )
            )
        data.streaming_content = ""
        data.streaming_reasoning = ""
    elif isinstance(change, SetPendingTool):
        data.pending_tool = change.call
        data.pending_permission = clone_permission_request(change.request)
    elif isinstance(change, SetCurrentTool):
        data.current_tool = change.call
    elif isinstance(change, SetToolCallBatch):
        data.tool_batch = ToolCallBatch(id=change.id, calls=list(change.calls), index=0)
    elif isinstance(change, AdvanceToolCallBatch):
        if data.tool_batch is None or data.tool_batch.index >= len(data.tool_batch.calls):
            raise InvariantViolationError("cannot advance an empty tool batch")
        data.tool_batch.index += 1
    elif isinstance(change, ClearPendingTool):
        data.pending_tool = None
        data.pending_permission = None
    elif isinstance(change, ClearCurrentTool):
        data.current_tool = None
    elif isinstance(change, ClearToolCallBatch):
        data.tool_batch = None
    elif isinstance(change, ResetConversation):
        data.messages = system_messages(data.messages)
        data.pending_tool = None
        data.pending_permission = None
        data.current_tool = None
        data.tool_batch = None
        data.streaming_content = ""
        data.streaming_reasoning = ""
    else:
        raise InvariantViolationError(f"unknown runtime data change {type(change).__name__}")


def clone_runtime_data(runtime_data: RuntimeData) -> RuntimeData:
    """Copy the parts of ``runtime_data`` a change is allowed to touch."""
    return RuntimeData(
        state=runtime_data.state,
        messages=list(runtime_data.messages),
        pending_tool=clone_tool_call(runtime_data.pending_tool),
        pending_permission=clone_permission_request(runtime_data.pending_permission),
        current_tool=clone_tool_call(runtime_data.current_tool),
        tool_batch=clone_tool_batch(runtime_data.tool_batch),
        streaming_content=runtime_data.streaming_content,
        streaming_reasoning=runtime_data.streaming_reasoning,
    )


def clone_message(message: Message) -> Message:
    """Return ``message``.

    Every field here is immutable — the sequence fields are tuples of frozen
    values — so there is nothing to copy, and the call sites can keep the
    clone-shaped vocabulary.
    """
    return message


def clone_tool_call(call: ToolCall | None) -> ToolCall | None:
    """Return ``call``; see :func:`clone_message`."""
    return call


def clone_permission_request(request: PermissionRequest | None) -> PermissionRequest | None:
    """Return ``request``; see :func:`clone_message`."""
    return request


def clone_tool_batch(batch: ToolCallBatch | None) -> ToolCallBatch | None:
    """Copy a batch, whose call list a change may extend or re-index."""
    if batch is None:
        return None
    return ToolCallBatch(id=batch.id, calls=list(batch.calls), index=batch.index)


def system_messages(messages: list[Message]) -> list[Message]:
    """Keep only the ``system`` messages, in order.

    Reset uses this so project instructions survive, and replay applies the same
    rule, which is why a reset also survives a restart.
    """
    return [message for message in messages if message.role == ROLE_SYSTEM]
