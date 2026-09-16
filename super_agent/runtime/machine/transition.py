"""The transition table, as one package-private static registry.

The registry is keyed by ``(state, event_kind)``, and the concrete event type of
each handler is read from the handler's second-parameter annotation by
:func:`adapt_transition`. The ``isinstance`` check it installs keeps a
mis-registration returning a ``ProtocolViolationError`` rather than failing in
some unrelated way later.

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
    STATE_ADVANCING_QUEUE,
    STATE_IDLE,
    STATE_INITIALIZING,
    STATE_RUNNING_TOOL,
    STATE_WAITING_APPROVAL,
    STATE_WAITING_LLM,
    ZERO_STATE,
    State,
)
from super_agent.runtime.protocol.types import ROLE_ASSISTANT, Message, ToolCall

#: A handler for one concrete event type.
EventHandler = Callable[[MachineSnapshot, Any], "TransitionResult"]
#: A registry entry: it accepts any event and rejects the ones it does not own.
TransitionHandler = Callable[[MachineSnapshot, Event], "TransitionResult"]


@dataclasses.dataclass(frozen=True, slots=True)
class TransitionResult:
    """A pure state-machine decision: state, data changes, and queue plan."""

    next_state: State
    runtime_data_changes: tuple[RuntimeDataChange, ...] = ()
    action_plan: ActionPlan = dataclasses.field(default_factory=ActionPlan)


@dataclasses.dataclass(frozen=True, slots=True)
class TransitionKey:
    state: State
    event_kind: str


def adapt_transition[E: Event](handler: Callable[[MachineSnapshot, E], TransitionResult]) -> TransitionHandler:
    """Wrap a concrete-event handler so the registry can hold it.

    The event type is read from the handler's second parameter annotation.
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
        (TransitionKey(STATE_INITIALIZING, EngineReady.kind), adapt_transition(handle_engine_ready)),
        (
            TransitionKey(STATE_IDLE, UserMessageSubmitted.kind),
            adapt_transition(handle_user_message_submitted),
        ),
        (
            TransitionKey(STATE_WAITING_LLM, AssistantMessageReceived.kind),
            adapt_transition(handle_assistant_message_received),
        ),
        (
            TransitionKey(STATE_WAITING_LLM, ToolBatchReceived.kind),
            adapt_transition(handle_tool_batch_received),
        ),
        (
            TransitionKey(STATE_WAITING_APPROVAL, ApprovalGranted.kind),
            adapt_transition(handle_approval_granted),
        ),
        (
            TransitionKey(STATE_WAITING_APPROVAL, ApprovalAlwaysGranted.kind),
            adapt_transition(handle_approval_always_granted),
        ),
        (
            TransitionKey(STATE_WAITING_APPROVAL, ApprovalDenied.kind),
            adapt_transition(handle_approval_denied),
        ),
        (
            TransitionKey(STATE_RUNNING_TOOL, ToolResultReceived.kind),
            adapt_transition(handle_tool_result_received),
        ),
        (
            TransitionKey(STATE_ADVANCING_QUEUE, ToolBatchFinished.kind),
            adapt_transition(handle_tool_batch_finished),
        ),
        (
            TransitionKey(STATE_ADVANCING_QUEUE, ToolCallNeedsApproval.kind),
            adapt_transition(handle_tool_call_needs_approval),
        ),
        (
            TransitionKey(STATE_ADVANCING_QUEUE, ToolCallReadyToRun.kind),
            adapt_transition(handle_tool_call_ready_to_run),
        ),
        (
            TransitionKey(STATE_ADVANCING_QUEUE, ToolCallDenied.kind),
            adapt_transition(handle_tool_call_denied),
        ),
        (TransitionKey(ZERO_STATE, ErrorOccurred.kind), adapt_transition(handle_error_occurred)),
        (TransitionKey(ZERO_STATE, CancelRequested.kind), adapt_transition(handle_cancel_requested)),
        (TransitionKey(ZERO_STATE, ResetRequested.kind), adapt_transition(handle_reset_requested)),
    )
    registry: dict[TransitionKey, TransitionHandler] = {}
    for key, handler in rules:
        register_transition(registry, key, handler)
    return registry


def transition(snapshot: MachineSnapshot, event: Event) -> TransitionResult:
    """Decide what ``event`` means in ``snapshot``'s state.

    Raises :class:`UnexpectedEventError` when the state does not accept the event
    and :class:`ProtocolViolationError` when the event's content does not match
    current data.
    """
    handler = _TRANSITION_REGISTRY.get(TransitionKey(snapshot.state, event.kind))
    if handler is None:
        handler = _TRANSITION_REGISTRY.get(TransitionKey(ZERO_STATE, event.kind))
    if handler is None:
        raise UnexpectedEventError(snapshot.state, event)
    return handler(snapshot, event)


def handle_engine_ready(_snapshot: MachineSnapshot, _event: EngineReady) -> TransitionResult:
    return TransitionResult(next_state=STATE_IDLE)


def handle_user_message_submitted(_snapshot: MachineSnapshot, event: UserMessageSubmitted) -> TransitionResult:
    return TransitionResult(
        next_state=STATE_WAITING_LLM,
        runtime_data_changes=(AppendUserMessage(content=event.content, attachments=event.attachments),),
        action_plan=ActionPlan(schedule=(CallModel(),)),
    )


def handle_assistant_message_received(_snapshot: MachineSnapshot, event: AssistantMessageReceived) -> TransitionResult:
    return TransitionResult(
        next_state=STATE_IDLE,
        runtime_data_changes=(
            AppendAssistantMessage(
                message=Message(
                    role=ROLE_ASSISTANT,
                    content=event.response.content,
                    reasoning_content=event.response.reasoning_content,
                )
            ),
        ),
    )


def handle_tool_batch_received(snapshot: MachineSnapshot, event: ToolBatchReceived) -> TransitionResult:
    if len(event.calls) == 0:
        raise ProtocolViolationError(snapshot.state, event, "tool batch is empty")
    return TransitionResult(
        next_state=STATE_ADVANCING_QUEUE,
        runtime_data_changes=(
            AppendAssistantMessage(
                message=Message(
                    role=ROLE_ASSISTANT,
                    content=event.content,
                    reasoning_content=event.reasoning_content,
                    tool_calls=tuple(event.calls),
                )
            ),
            SetToolCallBatch(id=_tool_batch_id(event.calls), calls=event.calls),
        ),
        action_plan=ActionPlan(schedule=(CheckToolQueue(),)),
    )


def handle_approval_granted(snapshot: MachineSnapshot, event: ApprovalGranted) -> TransitionResult:
    return _approve_tool(snapshot, event, event.call)


def handle_approval_always_granted(snapshot: MachineSnapshot, event: ApprovalAlwaysGranted) -> TransitionResult:
    return _approve_tool(snapshot, event, event.call)


def _approve_tool(snapshot: MachineSnapshot, event: Event, call: ToolCall) -> TransitionResult:
    if snapshot.pending_tool is None:
        raise ProtocolViolationError(snapshot.state, event, "approval has no pending tool")
    if not same_tool_call(call, snapshot.pending_tool):
        raise ProtocolViolationError(snapshot.state, event, "approved call does not match pending tool")
    return TransitionResult(
        next_state=STATE_RUNNING_TOOL,
        runtime_data_changes=(SetCurrentTool(call=call), ClearPendingTool()),
        action_plan=ActionPlan(schedule=(RunTool(call=call),)),
    )


def handle_approval_denied(snapshot: MachineSnapshot, event: ApprovalDenied) -> TransitionResult:
    if snapshot.pending_tool is None:
        raise ProtocolViolationError(snapshot.state, event, "denial has no pending tool")
    if not same_tool_call(event.call, snapshot.pending_tool):
        raise ProtocolViolationError(snapshot.state, event, "denied call does not match pending tool")
    return TransitionResult(
        next_state=STATE_ADVANCING_QUEUE,
        runtime_data_changes=(
            ClearPendingTool(),
            AppendToolResult(call=event.call, result="denied: " + event.call.name),
        ),
        action_plan=ActionPlan(schedule=(CheckToolQueue(),)),
    )


def handle_tool_result_received(snapshot: MachineSnapshot, event: ToolResultReceived) -> TransitionResult:
    if snapshot.current_tool is None:
        raise ProtocolViolationError(snapshot.state, event, "tool result has no current tool")
    if not same_tool_call(event.call, snapshot.current_tool):
        raise ProtocolViolationError(snapshot.state, event, "result call does not match current tool")
    return TransitionResult(
        next_state=STATE_ADVANCING_QUEUE,
        runtime_data_changes=(
            AppendToolResult(call=event.call, result=event.result),
            ClearCurrentTool(),
        ),
        action_plan=ActionPlan(schedule=(CheckToolQueue(),)),
    )


def handle_tool_batch_finished(snapshot: MachineSnapshot, event: ToolBatchFinished) -> TransitionResult:
    if not snapshot.queue.empty():
        raise ProtocolViolationError(snapshot.state, event, "tool batch finished before the queue was empty")
    return TransitionResult(
        next_state=STATE_WAITING_LLM,
        runtime_data_changes=(ClearToolCallBatch(),),
        action_plan=ActionPlan(schedule=(CallModel(),)),
    )


def handle_tool_call_needs_approval(snapshot: MachineSnapshot, event: ToolCallNeedsApproval) -> TransitionResult:
    if snapshot.queue.next is None:
        raise ProtocolViolationError(snapshot.state, event, "approval requested with no next tool")
    if not same_tool_call(event.call, snapshot.queue.next):
        raise ProtocolViolationError(snapshot.state, event, "approval call does not match next tool")
    return TransitionResult(
        next_state=STATE_WAITING_APPROVAL,
        runtime_data_changes=(
            SetPendingTool(call=event.call, request=event.request),
            AdvanceToolCallBatch(),
        ),
        action_plan=ActionPlan(schedule=(AwaitApproval(call=event.call, request=event.request),)),
    )


def handle_tool_call_ready_to_run(snapshot: MachineSnapshot, event: ToolCallReadyToRun) -> TransitionResult:
    if snapshot.queue.next is None:
        raise ProtocolViolationError(snapshot.state, event, "ready call has no next tool")
    if not same_tool_call(event.call, snapshot.queue.next):
        raise ProtocolViolationError(snapshot.state, event, "ready call does not match next tool")
    return TransitionResult(
        next_state=STATE_RUNNING_TOOL,
        runtime_data_changes=(AdvanceToolCallBatch(), SetCurrentTool(call=event.call)),
        action_plan=ActionPlan(schedule=(RunTool(call=event.call),)),
    )


def handle_tool_call_denied(snapshot: MachineSnapshot, event: ToolCallDenied) -> TransitionResult:
    if snapshot.queue.next is None:
        raise ProtocolViolationError(snapshot.state, event, "denied call has no next tool")
    if not same_tool_call(event.call, snapshot.queue.next):
        raise ProtocolViolationError(snapshot.state, event, "denied call does not match next tool")
    return TransitionResult(
        next_state=STATE_ADVANCING_QUEUE,
        runtime_data_changes=(
            AdvanceToolCallBatch(),
            AppendToolResult(call=event.call, result="denied by permission policy: " + event.reason),
        ),
        action_plan=ActionPlan(schedule=(CheckToolQueue(),)),
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
        results.append(AppendToolResult(call=snapshot.pending_tool, result=not_executed))
    if snapshot.current_tool is not None:
        results.append(AppendToolResult(call=snapshot.current_tool, result=running_result))
    for call in snapshot.queue.remaining:
        results.append(AppendToolResult(call=call, result=not_executed))
    return tuple(results)


def handle_error_occurred(snapshot: MachineSnapshot, event: ErrorOccurred) -> TransitionResult:
    reason = _runtime_error_message(event.err)
    return TransitionResult(
        next_state=STATE_IDLE,
        runtime_data_changes=(
            FlushStreamingAssistant(interrupted=True),
            *outstanding_tool_results(snapshot, reason, "not executed: " + reason),
            ClearPendingTool(),
            ClearCurrentTool(),
            ClearToolCallBatch(),
        ),
        action_plan=ActionPlan(clear_existing=True),
    )


def handle_cancel_requested(snapshot: MachineSnapshot, _event: CancelRequested) -> TransitionResult:
    return TransitionResult(
        next_state=STATE_IDLE,
        runtime_data_changes=(
            FlushStreamingAssistant(interrupted=True),
            *outstanding_tool_results(snapshot, "cancelled", "not executed: cancelled"),
            ClearPendingTool(),
            ClearCurrentTool(),
            ClearToolCallBatch(),
        ),
        action_plan=ActionPlan(clear_existing=True),
    )


def handle_reset_requested(_snapshot: MachineSnapshot, _event: ResetRequested) -> TransitionResult:
    return TransitionResult(
        next_state=STATE_IDLE,
        runtime_data_changes=(ResetConversation(),),
        action_plan=ActionPlan(clear_existing=True),
    )


def _tool_batch_id(calls: tuple[ToolCall, ...]) -> str:
    if len(calls) == 0 or calls[0].id == "":
        return "batch"
    return "batch-" + calls[0].id


def _runtime_error_message(error: BaseException | None) -> str:
    if error is None:
        return "unknown runtime error"
    return str(error)


#: Every rule, keyed by state and event kind. Built last because the registry
#: names the handlers above; a module-level call has to be sequenced after every
#: function it references is declared.
_TRANSITION_REGISTRY: Final[dict[TransitionKey, TransitionHandler]] = _new_transition_registry()
