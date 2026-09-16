"""Location-stable re-exports of the machine and protocol values.

These re-exports let the engine write ``execution.RunTool`` and
``execution.ApprovalGranted`` without importing ``runtime/machine`` itself.
The same names live at the same position here.
"""

from __future__ import annotations

from super_agent.runtime.machine.approval import (
    APPROVE_ALWAYS as APPROVE_ALWAYS,
    APPROVE_ONCE as APPROVE_ONCE,
    DENY_APPROVAL as DENY_APPROVAL,
    ApprovalDecision as ApprovalDecision,
)
from super_agent.runtime.machine.event import (
    ApprovalAlwaysGranted as ApprovalAlwaysGranted,
    ApprovalDenied as ApprovalDenied,
    ApprovalGranted as ApprovalGranted,
    AssistantMessageReceived as AssistantMessageReceived,
    Event as Event,
    ToolBatchFinished as ToolBatchFinished,
    ToolBatchReceived as ToolBatchReceived,
    ToolCallDenied as ToolCallDenied,
    ToolCallNeedsApproval as ToolCallNeedsApproval,
    ToolCallReadyToRun as ToolCallReadyToRun,
    ToolResultReceived as ToolResultReceived,
)
from super_agent.runtime.machine.runtime_data_change import (
    AppendStreamingAssistant as AppendStreamingAssistant,
)
from super_agent.runtime.machine.scheduled_action import (
    AwaitApproval as AwaitApproval,
    CallModel as CallModel,
    CheckToolQueue as CheckToolQueue,
    RunTool as RunTool,
    ScheduledAction as ScheduledAction,
)
from super_agent.runtime.machine.tool_batch import ToolCallBatch as ToolCallBatch
from super_agent.runtime.permission.types import (
    COMMAND_CLASS_DESTRUCTIVE as COMMAND_CLASS_DESTRUCTIVE,
    COMMAND_CLASS_NETWORK as COMMAND_CLASS_NETWORK,
    COMMAND_CLASS_READ_ONLY as COMMAND_CLASS_READ_ONLY,
    COMMAND_CLASS_UNKNOWN as COMMAND_CLASS_UNKNOWN,
    COMMAND_CLASS_WRITE as COMMAND_CLASS_WRITE,
    CommandClass as CommandClass,
    Request as PermissionRequest,
)
from super_agent.runtime.protocol.types import (
    Message as Message,
    Model as Model,
    ModelResponse as ModelResponse,
    StreamChunk as StreamChunk,
    ToolCall as ToolCall,
    ToolRunner as ToolRunner,
    ToolSpec as ToolSpec,
)

__all__ = [
    "APPROVE_ALWAYS",
    "APPROVE_ONCE",
    "COMMAND_CLASS_DESTRUCTIVE",
    "COMMAND_CLASS_NETWORK",
    "COMMAND_CLASS_READ_ONLY",
    "COMMAND_CLASS_UNKNOWN",
    "COMMAND_CLASS_WRITE",
    "DENY_APPROVAL",
    "AppendStreamingAssistant",
    "ApprovalAlwaysGranted",
    "ApprovalDecision",
    "ApprovalDenied",
    "ApprovalGranted",
    "AssistantMessageReceived",
    "AwaitApproval",
    "CallModel",
    "CheckToolQueue",
    "CommandClass",
    "Event",
    "Message",
    "Model",
    "ModelResponse",
    "PermissionRequest",
    "RunTool",
    "ScheduledAction",
    "StreamChunk",
    "ToolBatchFinished",
    "ToolBatchReceived",
    "ToolCall",
    "ToolCallBatch",
    "ToolCallDenied",
    "ToolCallNeedsApproval",
    "ToolCallReadyToRun",
    "ToolResultReceived",
    "ToolRunner",
    "ToolSpec",
]
