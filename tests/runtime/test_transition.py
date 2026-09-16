"""The transition table's exact output: state, change order, and action plan.

Counts catch missing or extra outputs; the type lists assert exact order, which is
the part of a transition that is easiest to get subtly wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from super_agent.runtime import machine
from super_agent.runtime.machine.transition import handle_engine_ready


def sample_tool_call() -> machine.ToolCall:
    return machine.ToolCall(id="call-1", name="bash", input="pwd")


def sample_tool_calls() -> tuple[machine.ToolCall, ...]:
    return (
        machine.ToolCall(id="call-1", name="first", input="a"),
        machine.ToolCall(id="call-2", name="second", input="b"),
    )


def transition_snapshot(state: machine.State, event: machine.Event) -> machine.MachineSnapshot:
    """Runtime data that satisfies ``state``'s invariants and ``event``'s guards."""
    call = sample_tool_call()
    candidate = getattr(event, "call", None)
    if isinstance(candidate, machine.ToolCall):
        call = candidate

    data = machine.RuntimeData(state=state)
    if state == machine.STATE_ADVANCING_QUEUE:
        data.tool_batch = machine.ToolCallBatch(calls=[call])
        if isinstance(event, machine.ToolBatchFinished):
            data.tool_batch.index = 1
    elif state == machine.STATE_WAITING_APPROVAL:
        data.pending_tool = call
        data.pending_permission = machine.PermissionRequest()
        data.tool_batch = machine.ToolCallBatch(calls=[call], index=1)
    elif state == machine.STATE_RUNNING_TOOL:
        data.current_tool = call
        data.tool_batch = machine.ToolCallBatch(calls=[call], index=1)
    return machine.snapshot_from(data)


@dataclass(frozen=True)
class TransitionCase:
    """One ``(State, Event) -> TransitionResult`` expectation."""

    state: machine.State
    event: machine.Event
    want_state: machine.State
    runtime_data_change_count: int = 0
    clear_existing_actions: bool = False
    scheduled_action_count: int = 0
    runtime_data_change_types: tuple[type[machine.RuntimeDataChange], ...] = ()
    scheduled_action_types: tuple[type[machine.ScheduledAction], ...] = ()
    want_error: bool = False

    def __post_init__(self) -> None:
        assert self.want_error or self.runtime_data_change_count == len(self.runtime_data_change_types)


def _cases() -> list[TransitionCase]:
    call = sample_tool_call()
    boom = RuntimeError("boom")
    return [
        # --- EngineReady ---
        TransitionCase(
            state=machine.STATE_INITIALIZING,
            event=machine.EngineReady(),
            want_state=machine.STATE_IDLE,
        ),
        # --- UserMessageSubmitted ---
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.UserMessageSubmitted(content="hi"),
            want_state=machine.STATE_WAITING_LLM,
            runtime_data_change_count=1,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AppendUserMessage,),
            scheduled_action_types=(machine.CallModel,),
        ),
        TransitionCase(
            state=machine.STATE_WAITING_LLM,
            event=machine.UserMessageSubmitted(content="hi"),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- AssistantMessageReceived ---
        TransitionCase(
            state=machine.STATE_WAITING_LLM,
            event=machine.AssistantMessageReceived(response=machine.ModelResponse(content="hi")),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=1,
            runtime_data_change_types=(machine.AppendAssistantMessage,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.AssistantMessageReceived(response=machine.ModelResponse(content="hi")),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ToolBatchReceived ---
        TransitionCase(
            state=machine.STATE_WAITING_LLM,
            event=machine.ToolBatchReceived(
                content="thinking", calls=sample_tool_calls(), reasoning_content="reasoning"
            ),
            want_state=machine.STATE_ADVANCING_QUEUE,
            runtime_data_change_count=2,  # AppendAssistantMessage + SetToolCallBatch
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AppendAssistantMessage, machine.SetToolCallBatch),
            scheduled_action_types=(machine.CheckToolQueue,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ToolBatchReceived(calls=sample_tool_calls()),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ToolBatchFinished ---
        TransitionCase(
            state=machine.STATE_ADVANCING_QUEUE,
            event=machine.ToolBatchFinished(),
            want_state=machine.STATE_WAITING_LLM,
            runtime_data_change_count=1,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.ClearToolCallBatch,),
            scheduled_action_types=(machine.CallModel,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ToolBatchFinished(),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ApprovalGranted ---
        TransitionCase(
            state=machine.STATE_WAITING_APPROVAL,
            event=machine.ApprovalGranted(call=call),
            want_state=machine.STATE_RUNNING_TOOL,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.SetCurrentTool, machine.ClearPendingTool),
            scheduled_action_types=(machine.RunTool,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ApprovalGranted(call=call),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ApprovalAlwaysGranted ---
        TransitionCase(
            state=machine.STATE_WAITING_APPROVAL,
            event=machine.ApprovalAlwaysGranted(call=call),
            want_state=machine.STATE_RUNNING_TOOL,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.SetCurrentTool, machine.ClearPendingTool),
            scheduled_action_types=(machine.RunTool,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ApprovalAlwaysGranted(call=call),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ApprovalDenied ---
        TransitionCase(
            state=machine.STATE_WAITING_APPROVAL,
            event=machine.ApprovalDenied(call=call),
            want_state=machine.STATE_ADVANCING_QUEUE,
            runtime_data_change_count=2,  # ClearPendingTool + AppendToolResult
            scheduled_action_count=1,
            runtime_data_change_types=(machine.ClearPendingTool, machine.AppendToolResult),
            scheduled_action_types=(machine.CheckToolQueue,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ApprovalDenied(call=call),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ToolResultReceived ---
        TransitionCase(
            state=machine.STATE_RUNNING_TOOL,
            event=machine.ToolResultReceived(call=call, result="ok"),
            want_state=machine.STATE_ADVANCING_QUEUE,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AppendToolResult, machine.ClearCurrentTool),
            scheduled_action_types=(machine.CheckToolQueue,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ToolResultReceived(call=call, result="ok"),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ToolCallNeedsApproval ---
        TransitionCase(
            state=machine.STATE_ADVANCING_QUEUE,
            event=machine.ToolCallNeedsApproval(call=call),
            want_state=machine.STATE_WAITING_APPROVAL,
            runtime_data_change_count=2,  # SetPendingTool + AdvanceToolCallBatch
            scheduled_action_count=1,
            runtime_data_change_types=(machine.SetPendingTool, machine.AdvanceToolCallBatch),
            scheduled_action_types=(machine.AwaitApproval,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ToolCallNeedsApproval(call=call),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ToolCallReadyToRun ---
        TransitionCase(
            state=machine.STATE_ADVANCING_QUEUE,
            event=machine.ToolCallReadyToRun(call=call),
            want_state=machine.STATE_RUNNING_TOOL,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AdvanceToolCallBatch, machine.SetCurrentTool),
            scheduled_action_types=(machine.RunTool,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ToolCallReadyToRun(call=call),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ToolCallDenied ---
        TransitionCase(
            state=machine.STATE_ADVANCING_QUEUE,
            event=machine.ToolCallDenied(call=call, reason="plan mode"),
            want_state=machine.STATE_ADVANCING_QUEUE,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AdvanceToolCallBatch, machine.AppendToolResult),
            scheduled_action_types=(machine.CheckToolQueue,),
        ),
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ToolCallDenied(call=call, reason="plan mode"),
            want_state=machine.STATE_IDLE,
            want_error=True,
        ),
        # --- ErrorOccurred ---
        # No tool call reached the transcript, so none may be answered: a tool
        # result without a matching tool call is rejected by the provider on the
        # next request.
        TransitionCase(
            state=machine.STATE_WAITING_LLM,
            event=machine.ErrorOccurred(err=boom),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=4,
            clear_existing_actions=True,
            runtime_data_change_types=(
                machine.FlushStreamingAssistant,
                machine.ClearPendingTool,
                machine.ClearCurrentTool,
                machine.ClearToolCallBatch,
            ),
        ),
        TransitionCase(
            state=machine.STATE_RUNNING_TOOL,
            event=machine.ErrorOccurred(err=boom),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=5,
            clear_existing_actions=True,
            runtime_data_change_types=(
                machine.FlushStreamingAssistant,
                machine.AppendToolResult,
                machine.ClearPendingTool,
                machine.ClearCurrentTool,
                machine.ClearToolCallBatch,
            ),
        ),
        TransitionCase(
            state=machine.STATE_ADVANCING_QUEUE,
            event=machine.ErrorOccurred(err=boom),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=5,
            clear_existing_actions=True,
            runtime_data_change_types=(
                machine.FlushStreamingAssistant,
                machine.AppendToolResult,
                machine.ClearPendingTool,
                machine.ClearCurrentTool,
                machine.ClearToolCallBatch,
            ),
        ),
        # --- CancelRequested ---
        TransitionCase(
            state=machine.STATE_WAITING_LLM,
            event=machine.CancelRequested(),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=4,
            clear_existing_actions=True,
            runtime_data_change_types=(
                machine.FlushStreamingAssistant,
                machine.ClearPendingTool,
                machine.ClearCurrentTool,
                machine.ClearToolCallBatch,
            ),
        ),
        # Cancelling while a call awaits approval must still answer that call,
        # exactly like the error path: an unanswered tool call makes the persisted
        # transcript unresumable.
        TransitionCase(
            state=machine.STATE_WAITING_APPROVAL,
            event=machine.CancelRequested(),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=5,
            clear_existing_actions=True,
            runtime_data_change_types=(
                machine.FlushStreamingAssistant,
                machine.AppendToolResult,
                machine.ClearPendingTool,
                machine.ClearCurrentTool,
                machine.ClearToolCallBatch,
            ),
        ),
        TransitionCase(
            state=machine.STATE_RUNNING_TOOL,
            event=machine.CancelRequested(),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=5,
            clear_existing_actions=True,
            runtime_data_change_types=(
                machine.FlushStreamingAssistant,
                machine.AppendToolResult,
                machine.ClearPendingTool,
                machine.ClearCurrentTool,
                machine.ClearToolCallBatch,
            ),
        ),
        TransitionCase(
            state=machine.STATE_ADVANCING_QUEUE,
            event=machine.CancelRequested(),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=5,
            clear_existing_actions=True,
            runtime_data_change_types=(
                machine.FlushStreamingAssistant,
                machine.AppendToolResult,
                machine.ClearPendingTool,
                machine.ClearCurrentTool,
                machine.ClearToolCallBatch,
            ),
        ),
        # --- ResetRequested ---
        TransitionCase(
            state=machine.STATE_IDLE,
            event=machine.ResetRequested(),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=1,
            clear_existing_actions=True,
            runtime_data_change_types=(machine.ResetConversation,),
        ),
        TransitionCase(
            state=machine.STATE_WAITING_LLM,
            event=machine.ResetRequested(),
            want_state=machine.STATE_IDLE,
            runtime_data_change_count=1,
            clear_existing_actions=True,
            runtime_data_change_types=(machine.ResetConversation,),
        ),
    ]


_CASES = _cases()


@pytest.mark.parametrize("case", _CASES, ids=[f"{case.state}+{type(case.event).__name__}" for case in _CASES])
def test_transition_table(case: TransitionCase) -> None:
    snapshot = transition_snapshot(case.state, case.event)
    if case.want_error:
        with pytest.raises((machine.UnexpectedEventError, machine.ProtocolViolationError)):
            machine.transition(snapshot, case.event)
        return

    result = machine.transition(snapshot, case.event)
    assert result.next_state == case.want_state
    assert len(result.runtime_data_changes) == case.runtime_data_change_count, result.runtime_data_changes
    assert result.action_plan.clear_existing == case.clear_existing_actions
    assert len(result.action_plan.schedule) == case.scheduled_action_count, result.action_plan.schedule
    got_changes = tuple(type(change) for change in result.runtime_data_changes)
    assert got_changes == case.runtime_data_change_types
    got_actions = tuple(type(action) for action in result.action_plan.schedule)
    assert got_actions == case.scheduled_action_types


def test_transition_rejects_an_event_with_no_registered_rule() -> None:
    """Every state rejects an event it has no rule for, with the right error."""
    snapshot = transition_snapshot(machine.STATE_INITIALIZING, machine.EngineReady())
    with pytest.raises(machine.UnexpectedEventError) as raised:
        machine.transition(snapshot, machine.ToolBatchFinished())
    assert raised.value.state == machine.STATE_INITIALIZING
    assert str(raised.value) == "state Initializing does not accept event ToolBatchFinished"


def test_registry_refuses_a_duplicate_key() -> None:
    """A new rule must not silently shadow an old one."""
    registry: dict[machine.TransitionKey, machine.TransitionHandler] = {}
    key = machine.TransitionKey(machine.STATE_IDLE, machine.EngineReady.kind)
    handler = machine.adapt_transition(handle_engine_ready)

    machine.register_transition(registry, key, handler)

    with pytest.raises(RuntimeError, match="duplicate state transition"):
        machine.register_transition(registry, key, handler)


def test_adapted_handler_rejects_an_event_of_the_wrong_type() -> None:
    """A mis-registration reports a protocol violation instead of misfiring."""
    handler = machine.adapt_transition(handle_engine_ready)
    snapshot = transition_snapshot(machine.STATE_INITIALIZING, machine.EngineReady())

    with pytest.raises(machine.ProtocolViolationError) as raised:
        handler(snapshot, machine.ResetRequested())

    assert raised.value.reason == "registered handler has incompatible event type"


def test_adapt_transition_refuses_a_handler_without_an_event_annotation() -> None:
    def unannotated(snapshot: machine.MachineSnapshot, event: object) -> machine.TransitionResult:
        return machine.TransitionResult(next_state=machine.STATE_IDLE)

    with pytest.raises(TypeError, match="cannot infer the event type"):
        machine.adapt_transition(unannotated)


def test_transition_result_carries_the_exact_change_values() -> None:
    """Type order is pinned above; this pins the payload the applier consumes."""
    event = machine.UserMessageSubmitted(content="hi")
    result = machine.transition(transition_snapshot(machine.STATE_IDLE, event), event)

    assert result.runtime_data_changes == (machine.AppendUserMessage(content="hi"),)

    event = machine.ToolCallDenied(call=sample_tool_call(), reason="plan mode")
    result = machine.transition(transition_snapshot(machine.STATE_ADVANCING_QUEUE, event), event)
    assert result.runtime_data_changes[1] == machine.AppendToolResult(
        call=sample_tool_call(), result="denied by permission policy: plan mode"
    )

    event = machine.ApprovalDenied(call=sample_tool_call())
    result = machine.transition(transition_snapshot(machine.STATE_WAITING_APPROVAL, event), event)
    assert result.runtime_data_changes[1] == machine.AppendToolResult(call=sample_tool_call(), result="denied: bash")


def test_cancel_answers_outstanding_calls_with_cancelled_text() -> None:
    """The cancel path answers every dispatched call, and says so."""
    event = machine.CancelRequested()
    result = machine.transition(transition_snapshot(machine.STATE_RUNNING_TOOL, event), event)

    results = [change for change in result.runtime_data_changes if isinstance(change, machine.AppendToolResult)]
    assert [change.result for change in results] == ["cancelled"]

    event = machine.ErrorOccurred(err=RuntimeError("boom"))
    result = machine.transition(transition_snapshot(machine.STATE_RUNNING_TOOL, event), event)
    results = [change for change in result.runtime_data_changes if isinstance(change, machine.AppendToolResult)]
    assert [change.result for change in results] == ["boom"]
