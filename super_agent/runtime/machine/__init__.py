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
    Attachment as Attachment,
    CommandClass as CommandClass,
    CommandClassDestructive as CommandClassDestructive,
    CommandClassNetwork as CommandClassNetwork,
    CommandClassReadOnly as CommandClassReadOnly,
    CommandClassUnknown as CommandClassUnknown,
    CommandClassWrite as CommandClassWrite,
    Message as Message,
    ModelResponse as ModelResponse,
    PermissionRequest as PermissionRequest,
    Role as Role,
    RoleAssistant as RoleAssistant,
    RoleSystem as RoleSystem,
    RoleTool as RoleTool,
    RoleUser as RoleUser,
    StreamChunk as StreamChunk,
    ToolCall as ToolCall,
    ZeroCommandClass as ZeroCommandClass,
)
from super_agent.runtime.machine.approval import (
    ApprovalDecision as ApprovalDecision,
    ApproveAlways as ApproveAlways,
    ApproveOnce as ApproveOnce,
    DenyApproval as DenyApproval,
)
from super_agent.runtime.machine.errors import (
    InvariantViolationError as InvariantViolationError,
    ProtocolViolationError as ProtocolViolationError,
    UnexpectedEventError as UnexpectedEventError,
)
from super_agent.runtime.machine.event import (
    AllEvents as AllEvents,
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
    AdvanceToolCallBatch as AdvanceToolCallBatch,
    AllRuntimeDataChanges as AllRuntimeDataChanges,
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
    AllScheduledActions as AllScheduledActions,
    AwaitApproval as AwaitApproval,
    CallModel as CallModel,
    CheckToolQueue as CheckToolQueue,
    RunTool as RunTool,
    ScheduledAction as ScheduledAction,
)
from super_agent.runtime.machine.snapshot import (
    MachineSnapshot as MachineSnapshot,
    QueueView as QueueView,
    SnapshotFrom as SnapshotFrom,
    ValidateRuntimeData as ValidateRuntimeData,
    same_tool_call as same_tool_call,
)
from super_agent.runtime.machine.state import (
    AllStates as AllStates,
    State as State,
    StateAdvancingQueue as StateAdvancingQueue,
    StateIdle as StateIdle,
    StateInitializing as StateInitializing,
    StateRunningTool as StateRunningTool,
    StateWaitingApproval as StateWaitingApproval,
    StateWaitingLLM as StateWaitingLLM,
    ZeroState as ZeroState,
)
from super_agent.runtime.machine.tool_batch import ToolCallBatch as ToolCallBatch
from super_agent.runtime.machine.transition import (
    EventHandler as EventHandler,
    Transition as Transition,
    TransitionHandler as TransitionHandler,
    TransitionKey as TransitionKey,
    TransitionResult as TransitionResult,
    adapt_transition as adapt_transition,
    outstanding_tool_results as outstanding_tool_results,
    register_transition as register_transition,
)
