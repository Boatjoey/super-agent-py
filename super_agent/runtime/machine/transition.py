"""The transition table, as one package-private static registry.

Go keys the registry by ``(state, eventKind)`` and infers the concrete event type
of each handler from a generic parameter. Python has no compile-time inference, so
:func:`adapt_transition` reads the handler's second-parameter annotation instead.
The ``isinstance`` check it installs is kept for the same reason Go keeps its
``ok`` check: a mis-registration returns a ``ProtocolViolationError`` rather than
failing in some unrelated way later.

The zero :class:`~super_agent.runtime.machine.state.State` is not a legal running
state, which is how the three global rows are registered once instead of six
times. Lookup tries the exact key first, then the zero-state key, and reports
``UnexpectedEventError`` if neither exists.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable
from typing import Any, Final, cast, get_type_hints

from super_agent.runtime.machine.action_plan import ActionPlan
from super_agent.runtime.machine.errors import ProtocolViolationError, UnexpectedEventError
from super_agent.runtime.machine.event import (
    ApprovalAlwaysGranted,
    ApprovalDenied,
    ApprovalGranted,
    AssistantMessageReceived,
    CancelRequested,
    EngineReady,
    ErrorOccurred,
    Event,
    ResetRequested,
    ToolBatchFinished,
    ToolBatchReceived,
    ToolCallDenied,
    ToolCallNeedsApproval,
    ToolCallReadyToRun,
    ToolResultReceived,
    UserMessageSubmitted,
)
from super_agent.runtime.machine.runtime_data_change import (
    AdvanceToolCallBatch,
    AppendAssistantMessage,
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
from super_agent.runtime.machine.scheduled_action import (
    AwaitApproval,
    CallModel,
    CheckToolQueue,
    RunTool,
)
from super_agent.runtime.machine.snapshot import MachineSnapshot, same_tool_call
from super_agent.runtime.machine.state import (
    State,
    StateAdvancingQueue,
    StateIdle,
    StateInitializing,
    StateRunningTool,
    StateWaitingApproval,
    StateWaitingLLM,
    ZeroState,
)
from super_agent.runtime.protocol.types import Message, RoleAssistant, ToolCall

#: A handler for one concrete event type.
EventHandler = Callable[[MachineSnapshot, Any], "TransitionResult"]
#: A registry entry: it accepts any event and rejects the ones it does not own.
TransitionHandler = Callable[[MachineSnapshot, Event], "TransitionResult"]


@dataclasses.dataclass(frozen=True, slots=True)
class TransitionResult:
    """A pure state-machine decision: state, data changes, and queue plan."""

    NextState: State
    RuntimeDataChanges: tuple[RuntimeDataChange, ...] = ()
    ActionPlan: ActionPlan = dataclasses.field(default_factory=ActionPlan)


@dataclasses.dataclass(frozen=True, slots=True)
class TransitionKey:
    state: State
    event_kind: str


def adapt_transition[E: Event](handler: Callable[[MachineSnapshot, E], TransitionResult]) -> TransitionHandler:
    """Wrap a concrete-event handler so the registry can hold it.

    The event type is read from the handler's second parameter annotation, which
    is what Go infers from the generic parameter.
    """
    event_type = _event_type_of(handler)

    def adapted(snapshot: MachineSnapshot, event: Event) -> TransitionResult:
        if not isinstance(event, event_type):
            raise ProtocolViolationError(snapshot.state, event, "registered handler has incompatible event type")
        return handler(snapshot, cast("E", event))

    return adapted


def _event_type_of(handler: Callable[..., Any]) -> type[Event]:
    """Read the concrete event class a handler declares in its signature."""
    hints = get_type_hints(handler)
    parameters = list(inspect.signature(handler).parameters)
    for name in parameters[1:2]:
        annotation = hints.get(name)
        if isinstance(annotation, type) and issubclass(annotation, Event):
            return annotation
    raise TypeError(
        f"cannot infer the event type of {getattr(handler, '__name__', handler)!r}; "
        "annotate its second parameter with a concrete Event subclass"
    )


def register_transition(
    registry: dict[TransitionKey, TransitionHandler],
    key: TransitionKey,
    handler: TransitionHandler,
) -> None:
    """Add one rule, refusing to let a new rule silently shadow an old one."""
    if key in registry:
        raise RuntimeError(f"duplicate state transition: {key.state} + {key.event_kind}")
    registry[key] = handler


def _new_transition_registry() -> dict[TransitionKey, TransitionHandler]:
    """Build the complete static edge registry for the machine graph."""
    rules: tuple[tuple[TransitionKey, TransitionHandler], ...] = (
        (TransitionKey(StateInitializing, EngineReady.kind), adapt_transition(handle_engine_ready)),
        (
            TransitionKey(StateIdle, UserMessageSubmitted.kind),
            adapt_transition(handle_user_message_submitted),
        ),
        (
            TransitionKey(StateWaitingLLM, AssistantMessageReceived.kind),
            adapt_transition(handle_assistant_message_received),
        ),
        (
            TransitionKey(StateWaitingLLM, ToolBatchReceived.kind),
            adapt_transition(handle_tool_batch_received),
        ),
        (
            TransitionKey(StateWaitingApproval, ApprovalGranted.kind),
            adapt_transition(handle_approval_granted),
        ),
        (
            TransitionKey(StateWaitingApproval, ApprovalAlwaysGranted.kind),
            adapt_transition(handle_approval_always_granted),
        ),
        (
            TransitionKey(StateWaitingApproval, ApprovalDenied.kind),
            adapt_transition(handle_approval_denied),
        ),
        (
            TransitionKey(StateRunningTool, ToolResultReceived.kind),
            adapt_transition(handle_tool_result_received),
        ),
        (
            TransitionKey(StateAdvancingQueue, ToolBatchFinished.kind),
            adapt_transition(handle_tool_batch_finished),
        ),
        (
            TransitionKey(StateAdvancingQueue, ToolCallNeedsApproval.kind),
            adapt_transition(handle_tool_call_needs_approval),
        ),
        (
            TransitionKey(StateAdvancingQueue, ToolCallReadyToRun.kind),
            adapt_transition(handle_tool_call_ready_to_run),
        ),
        (
            TransitionKey(StateAdvancingQueue, ToolCallDenied.kind),
            adapt_transition(handle_tool_call_denied),
        ),
        (TransitionKey(ZeroState, ErrorOccurred.kind), adapt_transition(handle_error_occurred)),
        (TransitionKey(ZeroState, CancelRequested.kind), adapt_transition(handle_cancel_requested)),
        (TransitionKey(ZeroState, ResetRequested.kind), adapt_transition(handle_reset_requested)),
    )
    registry: dict[TransitionKey, TransitionHandler] = {}
    for key, handler in rules:
        register_transition(registry, key, handler)
    return registry


def Transition(snapshot: MachineSnapshot, event: Event) -> TransitionResult:
    """Decide what ``event`` means in ``snapshot``'s state.

    Raises :class:`UnexpectedEventError` when the state does not accept the event
    and :class:`ProtocolViolationError` when the event's content does not match
    current data.
    """
    handler = _TRANSITION_REGISTRY.get(TransitionKey(snapshot.state, event.kind))
    if handler is None:
        handler = _TRANSITION_REGISTRY.get(TransitionKey(ZeroState, event.kind))
    if handler is None:
        raise UnexpectedEventError(snapshot.state, event)
    return handler(snapshot, event)


def handle_engine_ready(_snapshot: MachineSnapshot, _event: EngineReady) -> TransitionResult:
    return TransitionResult(NextState=StateIdle)


def handle_user_message_submitted(_snapshot: MachineSnapshot, event: UserMessageSubmitted) -> TransitionResult:
    return TransitionResult(
        NextState=StateWaitingLLM,
        RuntimeDataChanges=(AppendUserMessage(Content=event.Content, Attachments=event.Attachments),),
        ActionPlan=ActionPlan(Schedule=(CallModel(),)),
    )


def handle_assistant_message_received(_snapshot: MachineSnapshot, event: AssistantMessageReceived) -> TransitionResult:
    return TransitionResult(
        NextState=StateIdle,
        RuntimeDataChanges=(
            AppendAssistantMessage(
                Message=Message(
                    Role=RoleAssistant,
                    Content=event.Response.Content,
                    ReasoningContent=event.Response.ReasoningContent,
                )
            ),
        ),
    )


def handle_tool_batch_received(snapshot: MachineSnapshot, event: ToolBatchReceived) -> TransitionResult:
    if len(event.Calls) == 0:
        raise ProtocolViolationError(snapshot.state, event, "tool batch is empty")
    return TransitionResult(
        NextState=StateAdvancingQueue,
        RuntimeDataChanges=(
            AppendAssistantMessage(
                Message=Message(
                    Role=RoleAssistant,
                    Content=event.Content,
                    ReasoningContent=event.ReasoningContent,
                    ToolCalls=tuple(event.Calls),
                )
            ),
            SetToolCallBatch(ID=_tool_batch_id(event.Calls), Calls=event.Calls),
        ),
        ActionPlan=ActionPlan(Schedule=(CheckToolQueue(),)),
    )


def handle_approval_granted(snapshot: MachineSnapshot, event: ApprovalGranted) -> TransitionResult:
    return _approve_tool(snapshot, event, event.Call)


def handle_approval_always_granted(snapshot: MachineSnapshot, event: ApprovalAlwaysGranted) -> TransitionResult:
    return _approve_tool(snapshot, event, event.Call)


def _approve_tool(snapshot: MachineSnapshot, event: Event, call: ToolCall) -> TransitionResult:
    if snapshot.pending_tool is None:
        raise ProtocolViolationError(snapshot.state, event, "approval has no pending tool")
    if not same_tool_call(call, snapshot.pending_tool):
        raise ProtocolViolationError(snapshot.state, event, "approved call does not match pending tool")
    return TransitionResult(
        NextState=StateRunningTool,
        RuntimeDataChanges=(SetCurrentTool(Call=call), ClearPendingTool()),
        ActionPlan=ActionPlan(Schedule=(RunTool(Call=call),)),
    )


def handle_approval_denied(snapshot: MachineSnapshot, event: ApprovalDenied) -> TransitionResult:
    if snapshot.pending_tool is None:
        raise ProtocolViolationError(snapshot.state, event, "denial has no pending tool")
    if not same_tool_call(event.Call, snapshot.pending_tool):
        raise ProtocolViolationError(snapshot.state, event, "denied call does not match pending tool")
    return TransitionResult(
        NextState=StateAdvancingQueue,
        RuntimeDataChanges=(
            ClearPendingTool(),
            AppendToolResult(Call=event.Call, Result="denied: " + event.Call.Name),
        ),
        ActionPlan=ActionPlan(Schedule=(CheckToolQueue(),)),
    )


def handle_tool_result_received(snapshot: MachineSnapshot, event: ToolResultReceived) -> TransitionResult:
    if snapshot.current_tool is None:
        raise ProtocolViolationError(snapshot.state, event, "tool result has no current tool")
    if not same_tool_call(event.Call, snapshot.current_tool):
        raise ProtocolViolationError(snapshot.state, event, "result call does not match current tool")
    return TransitionResult(
        NextState=StateAdvancingQueue,
        RuntimeDataChanges=(
            AppendToolResult(Call=event.Call, Result=event.Result),
            ClearCurrentTool(),
        ),
        ActionPlan=ActionPlan(Schedule=(CheckToolQueue(),)),
    )


def handle_tool_batch_finished(snapshot: MachineSnapshot, event: ToolBatchFinished) -> TransitionResult:
    if not snapshot.queue.empty():
        raise ProtocolViolationError(snapshot.state, event, "tool batch finished before the queue was empty")
    return TransitionResult(
        NextState=StateWaitingLLM,
        RuntimeDataChanges=(ClearToolCallBatch(),),
        ActionPlan=ActionPlan(Schedule=(CallModel(),)),
    )


def handle_tool_call_needs_approval(snapshot: MachineSnapshot, event: ToolCallNeedsApproval) -> TransitionResult:
    if snapshot.queue.next is None:
        raise ProtocolViolationError(snapshot.state, event, "approval requested with no next tool")
    if not same_tool_call(event.Call, snapshot.queue.next):
        raise ProtocolViolationError(snapshot.state, event, "approval call does not match next tool")
    return TransitionResult(
        NextState=StateWaitingApproval,
        RuntimeDataChanges=(
            SetPendingTool(Call=event.Call, Request=event.Request),
            AdvanceToolCallBatch(),
        ),
        ActionPlan=ActionPlan(Schedule=(AwaitApproval(Call=event.Call, Request=event.Request),)),
    )


def handle_tool_call_ready_to_run(snapshot: MachineSnapshot, event: ToolCallReadyToRun) -> TransitionResult:
    if snapshot.queue.next is None:
        raise ProtocolViolationError(snapshot.state, event, "ready call has no next tool")
    if not same_tool_call(event.Call, snapshot.queue.next):
        raise ProtocolViolationError(snapshot.state, event, "ready call does not match next tool")
    return TransitionResult(
        NextState=StateRunningTool,
        RuntimeDataChanges=(AdvanceToolCallBatch(), SetCurrentTool(Call=event.Call)),
        ActionPlan=ActionPlan(Schedule=(RunTool(Call=event.Call),)),
    )


def handle_tool_call_denied(snapshot: MachineSnapshot, event: ToolCallDenied) -> TransitionResult:
    if snapshot.queue.next is None:
        raise ProtocolViolationError(snapshot.state, event, "denied call has no next tool")
    if not same_tool_call(event.Call, snapshot.queue.next):
        raise ProtocolViolationError(snapshot.state, event, "denied call does not match next tool")
    return TransitionResult(
        NextState=StateAdvancingQueue,
        RuntimeDataChanges=(
            AdvanceToolCallBatch(),
            AppendToolResult(Call=event.Call, Result="denied by permission policy: " + event.Reason),
        ),
        ActionPlan=ActionPlan(Schedule=(CheckToolQueue(),)),
    )


def outstanding_tool_results(
    snapshot: MachineSnapshot, running_result: str, not_executed: str
) -> tuple[RuntimeDataChange, ...]:
    """Answer every tool call the model asked for but that is still unanswered.

    A dispatched call is always in exactly one of three places: awaiting
    approval, running, or not yet reached by the batch, so the outstanding set is
    the pending call, the current call, and every remaining batch call.

    Every tool call the model asked for must be answered. An unanswered call
    produces a transcript the provider rejects with a 400, and because the
    transcript is persisted that failure survives a resume. Cancelling is
    therefore just as bound by this rule as failing.
    """
    results: list[RuntimeDataChange] = []
    if snapshot.pending_tool is not None:
        results.append(AppendToolResult(Call=snapshot.pending_tool, Result=not_executed))
    if snapshot.current_tool is not None:
        results.append(AppendToolResult(Call=snapshot.current_tool, Result=running_result))
    for call in snapshot.queue.remaining:
        results.append(AppendToolResult(Call=call, Result=not_executed))
    return tuple(results)


def handle_error_occurred(snapshot: MachineSnapshot, event: ErrorOccurred) -> TransitionResult:
    reason = _runtime_error_message(event.Err)
    return TransitionResult(
        NextState=StateIdle,
        RuntimeDataChanges=(
            FlushStreamingAssistant(Interrupted=True),
            *outstanding_tool_results(snapshot, reason, "not executed: " + reason),
            ClearPendingTool(),
            ClearCurrentTool(),
            ClearToolCallBatch(),
        ),
        ActionPlan=ActionPlan(ClearExisting=True),
    )


def handle_cancel_requested(snapshot: MachineSnapshot, _event: CancelRequested) -> TransitionResult:
    return TransitionResult(
        NextState=StateIdle,
        RuntimeDataChanges=(
            FlushStreamingAssistant(Interrupted=True),
            *outstanding_tool_results(snapshot, "cancelled", "not executed: cancelled"),
            ClearPendingTool(),
            ClearCurrentTool(),
            ClearToolCallBatch(),
        ),
        ActionPlan=ActionPlan(ClearExisting=True),
    )


def handle_reset_requested(_snapshot: MachineSnapshot, _event: ResetRequested) -> TransitionResult:
    return TransitionResult(
        NextState=StateIdle,
        RuntimeDataChanges=(ResetConversation(),),
        ActionPlan=ActionPlan(ClearExisting=True),
    )


def _tool_batch_id(calls: tuple[ToolCall, ...]) -> str:
    if len(calls) == 0 or calls[0].ID == "":
        return "batch"
    return "batch-" + calls[0].ID


def _runtime_error_message(error: BaseException | None) -> str:
    if error is None:
        return "unknown runtime error"
    return str(error)


#: Every rule, keyed by state and event kind. Built last because the registry
#: names the handlers above; Go's package-level initialisation runs after every
#: function is declared, and a module-level call here has to be sequenced the
#: same way by hand.
_TRANSITION_REGISTRY: Final[dict[TransitionKey, TransitionHandler]] = _new_transition_registry()
