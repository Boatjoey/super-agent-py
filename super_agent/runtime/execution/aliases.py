"""Location-stable re-exports of the machine and protocol values.

Go's ``execution/aliases.go`` exists so the engine can write ``execution.RunTool``
and ``execution.ApprovalGranted`` without importing ``runtime/machine`` itself.
The same names live at the same position here.
"""

from __future__ import annotations

from super_agent.runtime.machine.approval import (
    ApprovalDecision as ApprovalDecision,
    ApproveAlways as ApproveAlways,
    ApproveOnce as ApproveOnce,
    DenyApproval as DenyApproval,
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
    CommandClass as CommandClass,
    CommandClassDestructive as CommandClassDestructive,
    CommandClassNetwork as CommandClassNetwork,
    CommandClassReadOnly as CommandClassReadOnly,
    CommandClassUnknown as CommandClassUnknown,
    CommandClassWrite as CommandClassWrite,
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
    "AppendStreamingAssistant",
    "ApprovalAlwaysGranted",
    "ApprovalDecision",
    "ApprovalDenied",
    "ApprovalGranted",
    "ApproveAlways",
    "ApproveOnce",
    "AssistantMessageReceived",
    "AwaitApproval",
    "CallModel",
    "CheckToolQueue",
    "CommandClass",
    "CommandClassDestructive",
    "CommandClassNetwork",
    "CommandClassReadOnly",
    "CommandClassUnknown",
    "CommandClassWrite",
    "DenyApproval",
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
