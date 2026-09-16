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
from super_agent.runtime.machine.snapshot import ValidateRuntimeData
from super_agent.runtime.machine.tool_batch import ToolCallBatch
from super_agent.runtime.permission.types import Request as PermissionRequest
from super_agent.runtime.protocol.types import (
    Message,
    RoleAssistant,
    RoleSystem,
    RoleTool,
    RoleUser,
    ToolCall,
)


@dataclasses.dataclass(frozen=True, slots=True)
class RuntimeDataChangeResult:
    """The validated runtime data a transition should commit."""

    RuntimeData: RuntimeData


class RuntimeDataChangeApplier(Protocol):
    """The engine's port for turning a decision into committed data."""

    def ApplyRuntimeDataChanges(
        self, runtime_data: RuntimeData, result: TransitionResult
    ) -> RuntimeDataChangeResult: ...


class DefaultRuntimeDataChangeApplier:
    """The only applier; the port exists so tests can substitute one."""

    def ApplyRuntimeDataChanges(self, runtime_data: RuntimeData, result: TransitionResult) -> RuntimeDataChangeResult:
        """Clone ``runtime_data``, apply the transition's changes, and validate."""
        next_data = clone_runtime_data(runtime_data)
        next_data.State = result.NextState
        for change in result.RuntimeDataChanges:
            _apply_runtime_data_change(next_data, change)
        ValidateRuntimeData(next_data)
        return RuntimeDataChangeResult(RuntimeData=next_data)


def _apply_runtime_data_change(data: RuntimeData, change: RuntimeDataChange) -> None:
    if isinstance(change, AppendUserMessage):
        data.StreamingContent = ""
        data.StreamingReasoning = ""
        data.Messages.append(Message(Role=RoleUser, Content=change.Content, Attachments=change.Attachments))
    elif isinstance(change, AppendAssistantMessage):
        data.StreamingContent = ""
        data.StreamingReasoning = ""
        data.Messages.append(clone_message(change.Message))
    elif isinstance(change, AppendToolResult):
        data.StreamingContent = ""
        data.StreamingReasoning = ""
        data.Messages.append(
            Message(
                Role=RoleTool,
                Content=change.Result,
                ToolCallID=change.Call.ID,
                ToolName=change.Call.Name,
            )
        )
    elif isinstance(change, AppendStreamingAssistant):
        data.StreamingContent += change.Chunk.ContentDelta
        data.StreamingReasoning += change.Chunk.ReasoningContentDelta
    elif isinstance(change, FlushStreamingAssistant):
        if data.StreamingContent != "" or data.StreamingReasoning != "":
            data.Messages.append(
                Message(
                    Role=RoleAssistant,
                    Content=data.StreamingContent,
                    ReasoningContent=data.StreamingReasoning,
                    Interrupted=change.Interrupted,
                )
            )
        data.StreamingContent = ""
        data.StreamingReasoning = ""
    elif isinstance(change, SetPendingTool):
        data.PendingTool = change.Call
        data.PendingPermission = clone_permission_request(change.Request)
    elif isinstance(change, SetCurrentTool):
        data.CurrentTool = change.Call
    elif isinstance(change, SetToolCallBatch):
        data.ToolBatch = ToolCallBatch(ID=change.ID, Calls=list(change.Calls), Index=0)
    elif isinstance(change, AdvanceToolCallBatch):
        if data.ToolBatch is None or data.ToolBatch.Index >= len(data.ToolBatch.Calls):
            raise InvariantViolationError("cannot advance an empty tool batch")
        data.ToolBatch.Index += 1
    elif isinstance(change, ClearPendingTool):
        data.PendingTool = None
        data.PendingPermission = None
    elif isinstance(change, ClearCurrentTool):
        data.CurrentTool = None
    elif isinstance(change, ClearToolCallBatch):
        data.ToolBatch = None
    elif isinstance(change, ResetConversation):
        data.Messages = system_messages(data.Messages)
        data.PendingTool = None
        data.PendingPermission = None
        data.CurrentTool = None
        data.ToolBatch = None
        data.StreamingContent = ""
        data.StreamingReasoning = ""
    else:
        raise InvariantViolationError(f"unknown runtime data change {type(change).__name__}")


def clone_runtime_data(runtime_data: RuntimeData) -> RuntimeData:
    """Copy the parts of ``runtime_data`` a change is allowed to touch."""
    return RuntimeData(
        State=runtime_data.State,
        Messages=list(runtime_data.Messages),
        PendingTool=clone_tool_call(runtime_data.PendingTool),
        PendingPermission=clone_permission_request(runtime_data.PendingPermission),
        CurrentTool=clone_tool_call(runtime_data.CurrentTool),
        ToolBatch=clone_tool_batch(runtime_data.ToolBatch),
        StreamingContent=runtime_data.StreamingContent,
        StreamingReasoning=runtime_data.StreamingReasoning,
    )


def clone_message(message: Message) -> Message:
    """Return ``message``.

    Go copies a ``Message`` because its slices would otherwise be shared. Every
    field here is immutable — the sequence fields are tuples of frozen values —
    so there is nothing to copy and the call sites still read like Go's.
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
    return ToolCallBatch(ID=batch.ID, Calls=list(batch.Calls), Index=batch.Index)


def system_messages(messages: list[Message]) -> list[Message]:
    """Keep only the ``system`` messages, in order.

    Reset uses this so project instructions survive, and replay applies the same
    rule, which is why a reset also survives a restart.
    """
    return [message for message in messages if message.Role == RoleSystem]
