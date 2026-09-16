"""The transition table's exact output: state, change order, and action plan.

Ported from ``tests/runtime/transition_test.go``. Counts catch missing or extra
outputs; the type lists assert exact order, which is the part of a transition
that is easiest to get subtly wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from super_agent.runtime import machine
from super_agent.runtime.machine import transition as transition_module


def sample_tool_call() -> machine.ToolCall:
    return machine.ToolCall(ID="call-1", Name="bash", Input="pwd")


def sample_tool_calls() -> tuple[machine.ToolCall, ...]:
    return (
        machine.ToolCall(ID="call-1", Name="first", Input="a"),
        machine.ToolCall(ID="call-2", Name="second", Input="b"),
    )


def transition_snapshot(state: machine.State, event: machine.Event) -> machine.MachineSnapshot:
    """Runtime data that satisfies ``state``'s invariants and ``event``'s guards."""
    call = sample_tool_call()
    candidate = getattr(event, "Call", None)
    if isinstance(candidate, machine.ToolCall):
        call = candidate

    data = machine.RuntimeData(State=state)
    if state == machine.StateAdvancingQueue:
        data.ToolBatch = machine.ToolCallBatch(Calls=[call])
        if isinstance(event, machine.ToolBatchFinished):
            data.ToolBatch.Index = 1
    elif state == machine.StateWaitingApproval:
        data.PendingTool = call
        data.PendingPermission = machine.PermissionRequest()
        data.ToolBatch = machine.ToolCallBatch(Calls=[call], Index=1)
    elif state == machine.StateRunningTool:
        data.CurrentTool = call
        data.ToolBatch = machine.ToolCallBatch(Calls=[call], Index=1)
    return machine.SnapshotFrom(data)


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
            state=machine.StateInitializing,
            event=machine.EngineReady(),
            want_state=machine.StateIdle,
        ),
        # --- UserMessageSubmitted ---
        TransitionCase(
            state=machine.StateIdle,
            event=machine.UserMessageSubmitted(Content="hi"),
            want_state=machine.StateWaitingLLM,
            runtime_data_change_count=1,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AppendUserMessage,),
            scheduled_action_types=(machine.CallModel,),
        ),
        TransitionCase(
            state=machine.StateWaitingLLM,
            event=machine.UserMessageSubmitted(Content="hi"),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- AssistantMessageReceived ---
        TransitionCase(
            state=machine.StateWaitingLLM,
            event=machine.AssistantMessageReceived(Response=machine.ModelResponse(Content="hi")),
            want_state=machine.StateIdle,
            runtime_data_change_count=1,
            runtime_data_change_types=(machine.AppendAssistantMessage,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.AssistantMessageReceived(Response=machine.ModelResponse(Content="hi")),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ToolBatchReceived ---
        TransitionCase(
            state=machine.StateWaitingLLM,
            event=machine.ToolBatchReceived(
                Content="thinking", Calls=sample_tool_calls(), ReasoningContent="reasoning"
            ),
            want_state=machine.StateAdvancingQueue,
            runtime_data_change_count=2,  # AppendAssistantMessage + SetToolCallBatch
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AppendAssistantMessage, machine.SetToolCallBatch),
            scheduled_action_types=(machine.CheckToolQueue,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.ToolBatchReceived(Calls=sample_tool_calls()),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ToolBatchFinished ---
        TransitionCase(
            state=machine.StateAdvancingQueue,
            event=machine.ToolBatchFinished(),
            want_state=machine.StateWaitingLLM,
            runtime_data_change_count=1,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.ClearToolCallBatch,),
            scheduled_action_types=(machine.CallModel,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.ToolBatchFinished(),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ApprovalGranted ---
        TransitionCase(
            state=machine.StateWaitingApproval,
            event=machine.ApprovalGranted(Call=call),
            want_state=machine.StateRunningTool,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.SetCurrentTool, machine.ClearPendingTool),
            scheduled_action_types=(machine.RunTool,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.ApprovalGranted(Call=call),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ApprovalAlwaysGranted ---
        TransitionCase(
            state=machine.StateWaitingApproval,
            event=machine.ApprovalAlwaysGranted(Call=call),
            want_state=machine.StateRunningTool,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.SetCurrentTool, machine.ClearPendingTool),
            scheduled_action_types=(machine.RunTool,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.ApprovalAlwaysGranted(Call=call),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ApprovalDenied ---
        TransitionCase(
            state=machine.StateWaitingApproval,
            event=machine.ApprovalDenied(Call=call),
            want_state=machine.StateAdvancingQueue,
            runtime_data_change_count=2,  # ClearPendingTool + AppendToolResult
            scheduled_action_count=1,
            runtime_data_change_types=(machine.ClearPendingTool, machine.AppendToolResult),
            scheduled_action_types=(machine.CheckToolQueue,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.ApprovalDenied(Call=call),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ToolResultReceived ---
        TransitionCase(
            state=machine.StateRunningTool,
            event=machine.ToolResultReceived(Call=call, Result="ok"),
            want_state=machine.StateAdvancingQueue,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AppendToolResult, machine.ClearCurrentTool),
            scheduled_action_types=(machine.CheckToolQueue,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.ToolResultReceived(Call=call, Result="ok"),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ToolCallNeedsApproval ---
        TransitionCase(
            state=machine.StateAdvancingQueue,
            event=machine.ToolCallNeedsApproval(Call=call),
            want_state=machine.StateWaitingApproval,
            runtime_data_change_count=2,  # SetPendingTool + AdvanceToolCallBatch
            scheduled_action_count=1,
            runtime_data_change_types=(machine.SetPendingTool, machine.AdvanceToolCallBatch),
            scheduled_action_types=(machine.AwaitApproval,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.ToolCallNeedsApproval(Call=call),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ToolCallReadyToRun ---
        TransitionCase(
            state=machine.StateAdvancingQueue,
            event=machine.ToolCallReadyToRun(Call=call),
            want_state=machine.StateRunningTool,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AdvanceToolCallBatch, machine.SetCurrentTool),
            scheduled_action_types=(machine.RunTool,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.ToolCallReadyToRun(Call=call),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ToolCallDenied ---
        TransitionCase(
            state=machine.StateAdvancingQueue,
            event=machine.ToolCallDenied(Call=call, Reason="plan mode"),
            want_state=machine.StateAdvancingQueue,
            runtime_data_change_count=2,
            scheduled_action_count=1,
            runtime_data_change_types=(machine.AdvanceToolCallBatch, machine.AppendToolResult),
            scheduled_action_types=(machine.CheckToolQueue,),
        ),
        TransitionCase(
            state=machine.StateIdle,
            event=machine.ToolCallDenied(Call=call, Reason="plan mode"),
            want_state=machine.StateIdle,
            want_error=True,
        ),
        # --- ErrorOccurred ---
        # No tool call reached the transcript, so none may be answered: a tool
        # result without a matching tool call is rejected by the provider on the
        # next request.
        TransitionCase(
            state=machine.StateWaitingLLM,
            event=machine.ErrorOccurred(Err=boom),
            want_state=machine.StateIdle,
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
            state=machine.StateRunningTool,
            event=machine.ErrorOccurred(Err=boom),
            want_state=machine.StateIdle,
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
            state=machine.StateAdvancingQueue,
            event=machine.ErrorOccurred(Err=boom),
            want_state=machine.StateIdle,
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
            state=machine.StateWaitingLLM,
            event=machine.CancelRequested(),
            want_state=machine.StateIdle,
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
            state=machine.StateWaitingApproval,
            event=machine.CancelRequested(),
            want_state=machine.StateIdle,
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
            state=machine.StateRunningTool,
            event=machine.CancelRequested(),
            want_state=machine.StateIdle,
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
            state=machine.StateAdvancingQueue,
            event=machine.CancelRequested(),
            want_state=machine.StateIdle,
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
            state=machine.StateIdle,
            event=machine.ResetRequested(),
            want_state=machine.StateIdle,
            runtime_data_change_count=1,
            clear_existing_actions=True,
            runtime_data_change_types=(machine.ResetConversation,),
        ),
        TransitionCase(
            state=machine.StateWaitingLLM,
            event=machine.ResetRequested(),
            want_state=machine.StateIdle,
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
            machine.Transition(snapshot, case.event)
        return

    result = machine.Transition(snapshot, case.event)
    assert result.NextState == case.want_state
    assert len(result.RuntimeDataChanges) == case.runtime_data_change_count, result.RuntimeDataChanges
    assert result.ActionPlan.ClearExisting == case.clear_existing_actions
    assert len(result.ActionPlan.Schedule) == case.scheduled_action_count, result.ActionPlan.Schedule
    got_changes = tuple(type(change) for change in result.RuntimeDataChanges)
    assert got_changes == case.runtime_data_change_types
    got_actions = tuple(type(action) for action in result.ActionPlan.Schedule)
    assert got_actions == case.scheduled_action_types


def test_transition_rejects_an_event_with_no_registered_rule() -> None:
    """Every state rejects an event it has no rule for, with the right error."""
    snapshot = transition_snapshot(machine.StateInitializing, machine.EngineReady())
    with pytest.raises(machine.UnexpectedEventError) as raised:
        machine.Transition(snapshot, machine.ToolBatchFinished())
    assert raised.value.State == machine.StateInitializing
    assert str(raised.value) == "state Initializing does not accept event ToolBatchFinished"


def test_registry_refuses_a_duplicate_key() -> None:
    """A new rule must not silently shadow an old one."""
    registry: dict[machine.TransitionKey, machine.TransitionHandler] = {}
    key = machine.TransitionKey(machine.StateIdle, machine.EngineReady.kind)
    handler = machine.adapt_transition(transition_module.handle_engine_ready)

    machine.register_transition(registry, key, handler)

    with pytest.raises(RuntimeError, match="duplicate state transition"):
        machine.register_transition(registry, key, handler)


def test_adapted_handler_rejects_an_event_of_the_wrong_type() -> None:
    """A mis-registration reports a protocol violation instead of misfiring."""
    handler = machine.adapt_transition(transition_module.handle_engine_ready)
    snapshot = transition_snapshot(machine.StateInitializing, machine.EngineReady())

    with pytest.raises(machine.ProtocolViolationError) as raised:
        handler(snapshot, machine.ResetRequested())

    assert raised.value.Reason == "registered handler has incompatible event type"


def test_adapt_transition_refuses_a_handler_without_an_event_annotation() -> None:
    def unannotated(snapshot: machine.MachineSnapshot, event: object) -> machine.TransitionResult:
        return machine.TransitionResult(NextState=machine.StateIdle)

    with pytest.raises(TypeError, match="cannot infer the event type"):
        machine.adapt_transition(unannotated)


def test_transition_result_carries_the_exact_change_values() -> None:
    """Type order is pinned above; this pins the payload the applier consumes."""
    event = machine.UserMessageSubmitted(Content="hi")
    result = machine.Transition(transition_snapshot(machine.StateIdle, event), event)

    assert result.RuntimeDataChanges == (machine.AppendUserMessage(Content="hi"),)

    event = machine.ToolCallDenied(Call=sample_tool_call(), Reason="plan mode")
    result = machine.Transition(transition_snapshot(machine.StateAdvancingQueue, event), event)
    assert result.RuntimeDataChanges[1] == machine.AppendToolResult(
        Call=sample_tool_call(), Result="denied by permission policy: plan mode"
    )

    event = machine.ApprovalDenied(Call=sample_tool_call())
    result = machine.Transition(transition_snapshot(machine.StateWaitingApproval, event), event)
    assert result.RuntimeDataChanges[1] == machine.AppendToolResult(Call=sample_tool_call(), Result="denied: bash")


def test_cancel_answers_outstanding_calls_with_cancelled_text() -> None:
    """The cancel path answers every dispatched call, and says so."""
    event = machine.CancelRequested()
    result = machine.Transition(transition_snapshot(machine.StateRunningTool, event), event)

    results = [change for change in result.RuntimeDataChanges if isinstance(change, machine.AppendToolResult)]
    assert [change.Result for change in results] == ["cancelled"]

    event = machine.ErrorOccurred(Err=RuntimeError("boom"))
    result = machine.Transition(transition_snapshot(machine.StateRunningTool, event), event)
    results = [change for change in result.RuntimeDataChanges if isinstance(change, machine.AppendToolResult)]
    assert [change.Result for change in results] == ["boom"]
