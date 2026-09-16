"""Scheduled-action execution and the permission policy.

``runtime/execution`` implements the outbound ports the engine drives: calling a
model, running a tool, deciding whether a call needs approval, and classifying a
command. A package spans several modules here, so this module stands in for the
package namespace.
"""

from __future__ import annotations

from super_agent.runtime.execution.action_queue import ActionQueue as ActionQueue
from super_agent.runtime.execution.action_result_resolver import (
    ActionResultInput as ActionResultInput,
    ActionResultResolver as ActionResultResolver,
    DefaultActionResultResolver as DefaultActionResultResolver,
)
from super_agent.runtime.execution.aliases import (
    APPROVE_ALWAYS as APPROVE_ALWAYS,
    APPROVE_ONCE as APPROVE_ONCE,
    COMMAND_CLASS_DESTRUCTIVE as COMMAND_CLASS_DESTRUCTIVE,
    COMMAND_CLASS_NETWORK as COMMAND_CLASS_NETWORK,
    COMMAND_CLASS_READ_ONLY as COMMAND_CLASS_READ_ONLY,
    COMMAND_CLASS_UNKNOWN as COMMAND_CLASS_UNKNOWN,
    COMMAND_CLASS_WRITE as COMMAND_CLASS_WRITE,
    DENY_APPROVAL as DENY_APPROVAL,
    AppendStreamingAssistant as AppendStreamingAssistant,
    ApprovalAlwaysGranted as ApprovalAlwaysGranted,
    ApprovalDecision as ApprovalDecision,
    ApprovalDenied as ApprovalDenied,
    ApprovalGranted as ApprovalGranted,
    AssistantMessageReceived as AssistantMessageReceived,
    AwaitApproval as AwaitApproval,
    CallModel as CallModel,
    CheckToolQueue as CheckToolQueue,
    CommandClass as CommandClass,
    Event as Event,
    Message as Message,
    Model as Model,
    ModelResponse as ModelResponse,
    PermissionRequest as PermissionRequest,
    RunTool as RunTool,
    ScheduledAction as ScheduledAction,
    StreamChunk as StreamChunk,
    ToolBatchFinished as ToolBatchFinished,
    ToolBatchReceived as ToolBatchReceived,
    ToolCall as ToolCall,
    ToolCallBatch as ToolCallBatch,
    ToolCallDenied as ToolCallDenied,
    ToolCallNeedsApproval as ToolCallNeedsApproval,
    ToolCallReadyToRun as ToolCallReadyToRun,
    ToolResultReceived as ToolResultReceived,
    ToolRunner as ToolRunner,
    ToolSpec as ToolSpec,
)
from super_agent.runtime.execution.approval_store import (
    ApprovalKey as ApprovalKey,
    ApprovalStore as ApprovalStore,
    MemoryApprovalStore as MemoryApprovalStore,
    hashCanonicalInput as hashCanonicalInput,
    new_approval_key as new_approval_key,
)
from super_agent.runtime.execution.command_analyzer import (
    analyzeCommandRequest as analyzeCommandRequest,
    commandEnv as commandEnv,
    commandMayWrite as commandMayWrite,
    commandPaths as commandPaths,
    containsNetworkIntent as containsNetworkIntent,
    firstNonEmpty as firstNonEmpty,
    hasAnyToken as hasAnyToken,
    isReadOnlyGitCommand as isReadOnlyGitCommand,
    jsonStringField as jsonStringField,
    toolPaths as toolPaths,
)
from super_agent.runtime.execution.policy import (
    PERMISSION_MODE_ACCEPT_EDITS as PERMISSION_MODE_ACCEPT_EDITS,
    PERMISSION_MODE_ASK as PERMISSION_MODE_ASK,
    PERMISSION_MODE_BYPASS as PERMISSION_MODE_BYPASS,
    PERMISSION_MODE_PLAN as PERMISSION_MODE_PLAN,
    ZERO_PERMISSION_MODE as ZERO_PERMISSION_MODE,
    DefaultPolicy as DefaultPolicy,
    PermissionMode as PermissionMode,
    PermissionRules as PermissionRules,
    Policy as Policy,
    ToolDecision as ToolDecision,
    ToolPolicyInput as ToolPolicyInput,
    isRiskyTool as isRiskyTool,
    new_default_policy as new_default_policy,
    new_policy as new_policy,
    valid_permission_mode as valid_permission_mode,
)
from super_agent.runtime.execution.run_controller import (
    ActionID as ActionID,
    DefaultRunController as DefaultRunController,
    RunController as RunController,
    RunID as RunID,
)
from super_agent.runtime.execution.scheduled_action_executor import (
    ERR_APPROVAL_DISMISSED as ERR_APPROVAL_DISMISSED,
    ApprovalDismissed as ApprovalDismissed,
    ApprovalWaiter as ApprovalWaiter,
    DefaultScheduledActionExecutor as DefaultScheduledActionExecutor,
    ScheduledActionExecutor as ScheduledActionExecutor,
    ScheduledActionInput as ScheduledActionInput,
)
from super_agent.runtime.execution.scheduled_action_result import (
    ALL_SCHEDULED_ACTION_RESULTS as ALL_SCHEDULED_ACTION_RESULTS,
    ApprovalReceived as ApprovalReceived,
    ModelReplied as ModelReplied,
    ScheduledActionResult as ScheduledActionResult,
    ToolFinished as ToolFinished,
    ToolQueueChecked as ToolQueueChecked,
)
from super_agent.runtime.execution.scheduled_action_runner import (
    ActionCompletion as ActionCompletion,
    DefaultScheduledActionRunner as DefaultScheduledActionRunner,
    QueuedAction as QueuedAction,
    ScheduledActionRunner as ScheduledActionRunner,
)
