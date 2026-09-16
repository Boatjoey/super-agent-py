"""The pure domain core.

States, events, runtime data, runtime-data changes, action plans, scheduled
actions, and transitions. No I/O, no locks, no model or tool calls — the
dependency rule in ``tests/architecture/test_dependencies.py`` (R7) enforces it.

A package spans several modules here, so this module stands in for the package
namespace: everything written as ``machine.X`` is re-exported here.
"""

from __future__ import annotations

from super_agent.runtime.machine.action_plan import ActionPlan as ActionPlan
from super_agent.runtime.machine.aliases import (
    COMMAND_CLASS_DESTRUCTIVE as COMMAND_CLASS_DESTRUCTIVE,
    COMMAND_CLASS_NETWORK as COMMAND_CLASS_NETWORK,
    COMMAND_CLASS_READ_ONLY as COMMAND_CLASS_READ_ONLY,
    COMMAND_CLASS_UNKNOWN as COMMAND_CLASS_UNKNOWN,
    COMMAND_CLASS_WRITE as COMMAND_CLASS_WRITE,
    ROLE_ASSISTANT as ROLE_ASSISTANT,
    ROLE_SYSTEM as ROLE_SYSTEM,
    ROLE_TOOL as ROLE_TOOL,
    ROLE_USER as ROLE_USER,
    ZERO_COMMAND_CLASS as ZERO_COMMAND_CLASS,
    Attachment as Attachment,
    CommandClass as CommandClass,
    Message as Message,
    ModelResponse as ModelResponse,
    PermissionRequest as PermissionRequest,
    Role as Role,
    StreamChunk as StreamChunk,
    ToolCall as ToolCall,
)
from super_agent.runtime.machine.approval import (
    APPROVE_ALWAYS as APPROVE_ALWAYS,
    APPROVE_ONCE as APPROVE_ONCE,
    DENY_APPROVAL as DENY_APPROVAL,
    ApprovalDecision as ApprovalDecision,
)
from super_agent.runtime.machine.errors import (
    InvariantViolationError as InvariantViolationError,
    ProtocolViolationError as ProtocolViolationError,
    UnexpectedEventError as UnexpectedEventError,
)
from super_agent.runtime.machine.event import (
    ALL_EVENTS as ALL_EVENTS,
    ApprovalAlwaysGranted as ApprovalAlwaysGranted,
    ApprovalDenied as ApprovalDenied,
    ApprovalGranted as ApprovalGranted,
    AssistantMessageReceived as AssistantMessageReceived,
    CancelRequested as CancelRequested,
    EngineReady as EngineReady,
    ErrorOccurred as ErrorOccurred,
    Event as Event,
    ResetRequested as ResetRequested,
    ToolBatchFinished as ToolBatchFinished,
    ToolBatchReceived as ToolBatchReceived,
    ToolCallDenied as ToolCallDenied,
    ToolCallNeedsApproval as ToolCallNeedsApproval,
    ToolCallReadyToRun as ToolCallReadyToRun,
    ToolResultReceived as ToolResultReceived,
    UserMessageSubmitted as UserMessageSubmitted,
)
from super_agent.runtime.machine.runtime_data import RuntimeData as RuntimeData
from super_agent.runtime.machine.runtime_data_change import (
    ALL_RUNTIME_DATA_CHANGES as ALL_RUNTIME_DATA_CHANGES,
    AdvanceToolCallBatch as AdvanceToolCallBatch,
    AppendAssistantMessage as AppendAssistantMessage,
    AppendStreamingAssistant as AppendStreamingAssistant,
    AppendToolResult as AppendToolResult,
    AppendUserMessage as AppendUserMessage,
    ClearCurrentTool as ClearCurrentTool,
    ClearPendingTool as ClearPendingTool,
    ClearToolCallBatch as ClearToolCallBatch,
    FlushStreamingAssistant as FlushStreamingAssistant,
    ResetConversation as ResetConversation,
    RuntimeDataChange as RuntimeDataChange,
    SetCurrentTool as SetCurrentTool,
    SetPendingTool as SetPendingTool,
    SetToolCallBatch as SetToolCallBatch,
)
from super_agent.runtime.machine.runtime_data_change_applier import (
    DefaultRuntimeDataChangeApplier as DefaultRuntimeDataChangeApplier,
    RuntimeDataChangeApplier as RuntimeDataChangeApplier,
    RuntimeDataChangeResult as RuntimeDataChangeResult,
    clone_message as clone_message,
    clone_permission_request as clone_permission_request,
    clone_runtime_data as clone_runtime_data,
    clone_tool_batch as clone_tool_batch,
    clone_tool_call as clone_tool_call,
    system_messages as system_messages,
)
from super_agent.runtime.machine.scheduled_action import (
    ALL_SCHEDULED_ACTIONS as ALL_SCHEDULED_ACTIONS,
    AwaitApproval as AwaitApproval,
    CallModel as CallModel,
    CheckToolQueue as CheckToolQueue,
    RunTool as RunTool,
    ScheduledAction as ScheduledAction,
)
from super_agent.runtime.machine.snapshot import (
    MachineSnapshot as MachineSnapshot,
    QueueView as QueueView,
    same_tool_call as same_tool_call,
    snapshot_from as snapshot_from,
    validate_runtime_data as validate_runtime_data,
)
from super_agent.runtime.machine.state import (
    ALL_STATES as ALL_STATES,
    STATE_ADVANCING_QUEUE as STATE_ADVANCING_QUEUE,
    STATE_IDLE as STATE_IDLE,
    STATE_INITIALIZING as STATE_INITIALIZING,
    STATE_RUNNING_TOOL as STATE_RUNNING_TOOL,
    STATE_WAITING_APPROVAL as STATE_WAITING_APPROVAL,
    STATE_WAITING_LLM as STATE_WAITING_LLM,
    ZERO_STATE as ZERO_STATE,
    State as State,
)
from super_agent.runtime.machine.tool_batch import ToolCallBatch as ToolCallBatch
from super_agent.runtime.machine.transition import (
    EventHandler as EventHandler,
    TransitionHandler as TransitionHandler,
    TransitionKey as TransitionKey,
    TransitionResult as TransitionResult,
    adapt_transition as adapt_transition,
    outstanding_tool_results as outstanding_tool_results,
    register_transition as register_transition,
    transition as transition,
)
