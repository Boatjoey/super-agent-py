"""Invariants, protocol guards, and the transactional applier.

The engine-level cases live in ``tests/runtime/test_engine.py``, because they need
the engine that this module does not construct.
"""

from __future__ import annotations

from typing import cast

import pytest

from super_agent.runtime import machine
from tests.runtime.test_transition import sample_tool_call, transition_snapshot


def test_snapshot_from_rejects_invalid_runtime_data() -> None:
    cases = {
        "AdvancingQueue-without-batch": machine.RuntimeData(State=machine.StateAdvancingQueue),
        "WaitingApproval-without-approval": machine.RuntimeData(State=machine.StateWaitingApproval),
        "RunningTool-without-tool": machine.RuntimeData(State=machine.StateRunningTool),
        "Idle-with-a-batch": machine.RuntimeData(State=machine.StateIdle, ToolBatch=machine.ToolCallBatch()),
    }
    for label, data in cases.items():
        with pytest.raises(machine.InvariantViolationError):
            machine.SnapshotFrom(data)
        assert label  # the label is the case identity in the failure message


def test_transition_classifies_state_mismatch_as_unexpected_event() -> None:
    event = machine.ToolResultReceived(Call=machine.ToolCall(ID="call-1"), Result="ok")
    with pytest.raises(machine.UnexpectedEventError):
        machine.Transition(transition_snapshot(machine.StateIdle, event), event)


def test_transition_rejects_approval_for_different_call() -> None:
    pending = machine.ToolCall(ID="call-1", Name="bash", Input="pwd")
    data = machine.RuntimeData(
        State=machine.StateWaitingApproval,
        PendingTool=pending,
        PendingPermission=machine.PermissionRequest(),
        ToolBatch=machine.ToolCallBatch(Calls=[pending], Index=1),
    )
    snapshot = machine.SnapshotFrom(data)
    with pytest.raises(machine.ProtocolViolationError) as raised:
        machine.Transition(
            snapshot, machine.ApprovalGranted(Call=machine.ToolCall(ID="call-2", Name="bash", Input="pwd"))
        )
    assert raised.value.Reason == "approved call does not match pending tool"


def test_transition_rejects_result_for_different_current_call() -> None:
    current = machine.ToolCall(ID="call-1", Name="bash", Input="pwd")
    data = machine.RuntimeData(
        State=machine.StateRunningTool,
        CurrentTool=current,
        ToolBatch=machine.ToolCallBatch(Calls=[current], Index=1),
    )
    snapshot = machine.SnapshotFrom(data)
    with pytest.raises(machine.ProtocolViolationError) as raised:
        machine.Transition(snapshot, machine.ToolResultReceived(Call=machine.ToolCall(ID="call-2"), Result="ok"))
    assert raised.value.Reason == "result call does not match current tool"


def test_transition_rejects_batch_finished_before_queue_empty() -> None:
    call = machine.ToolCall(ID="call-1")
    data = machine.RuntimeData(State=machine.StateAdvancingQueue, ToolBatch=machine.ToolCallBatch(Calls=[call]))
    snapshot = machine.SnapshotFrom(data)
    with pytest.raises(machine.ProtocolViolationError) as raised:
        machine.Transition(snapshot, machine.ToolBatchFinished())
    assert raised.value.Reason == "tool batch finished before the queue was empty"


def test_transition_rejects_an_empty_tool_batch() -> None:
    """An empty batch is a protocol violation, not a batch nobody queued."""
    snapshot = transition_snapshot(machine.StateWaitingLLM, machine.EngineReady())
    with pytest.raises(machine.ProtocolViolationError) as raised:
        machine.Transition(snapshot, machine.ToolBatchReceived(Calls=()))
    assert raised.value.Reason == "tool batch is empty"


def test_runtime_data_change_applier_does_not_mutate_original_state_when_validation_fails() -> None:
    """A rejected candidate leaves the committed data untouched."""
    call = machine.ToolCall(ID="call-1")
    original = machine.RuntimeData(State=machine.StateAdvancingQueue, ToolBatch=machine.ToolCallBatch(Calls=[call]))

    with pytest.raises(machine.InvariantViolationError):
        machine.DefaultRuntimeDataChangeApplier().ApplyRuntimeDataChanges(
            original,
            machine.TransitionResult(
                NextState=machine.StateRunningTool,
                RuntimeDataChanges=(machine.AdvanceToolCallBatch(),),
            ),
        )

    assert original.State == machine.StateAdvancingQueue
    assert original.ToolBatch is not None
    assert original.ToolBatch.Index == 0
    assert original.CurrentTool is None


def test_applier_raises_invariant_error_for_a_change_it_cannot_apply() -> None:
    with pytest.raises(machine.InvariantViolationError) as raised:
        machine.DefaultRuntimeDataChangeApplier().ApplyRuntimeDataChanges(
            machine.RuntimeData(State=machine.StateIdle),
            machine.TransitionResult(
                NextState=machine.StateIdle,
                RuntimeDataChanges=(machine.AdvanceToolCallBatch(),),
            ),
        )
    assert raised.value.Reason == "cannot advance an empty tool batch"


def test_applier_raises_invariant_error_for_an_unknown_change() -> None:
    """The applier's last line of defence: a change it does not recognise."""
    rogue = cast("machine.RuntimeDataChange", object())
    with pytest.raises(machine.InvariantViolationError) as raised:
        machine.DefaultRuntimeDataChangeApplier().ApplyRuntimeDataChanges(
            machine.RuntimeData(State=machine.StateIdle),
            machine.TransitionResult(NextState=machine.StateIdle, RuntimeDataChanges=(rogue,)),
        )
    assert raised.value.Reason == "unknown runtime data change object"


@pytest.mark.parametrize(
    "base",
    [machine.Event, machine.RuntimeDataChange, machine.ScheduledAction],
    ids=["Event", "RuntimeDataChange", "ScheduledAction"],
)
def test_variant_sets_are_sealed(base: type[object]) -> None:
    """The type sets are closed, so a class outside the variant set cannot join."""
    with pytest.raises(TypeError, match="sealed"):

        class Rogue(base):  # type: ignore[misc, valid-type]
            pass


def test_clone_runtime_data_does_not_alias_the_tool_batch() -> None:
    """The transaction only works if the clone is genuinely separate."""
    batch = machine.ToolCallBatch(Calls=[sample_tool_call()], Index=0)
    original = machine.RuntimeData(State=machine.StateAdvancingQueue, ToolBatch=batch)

    cloned = machine.clone_runtime_data(original)
    assert cloned.ToolBatch is not None
    cloned.ToolBatch.Index = 1

    assert original.ToolBatch is not None
    assert original.ToolBatch.Index == 0


def test_reset_conversation_preserves_system_messages() -> None:
    """Project instructions must survive a reset, and replay applies the same rule."""
    system = machine.Message(Role=machine.RoleSystem, Content="instructions")
    original = machine.RuntimeData(
        State=machine.StateIdle,
        Messages=[
            system,
            machine.Message(Role=machine.RoleUser, Content="hi"),
            machine.Message(Role=machine.RoleAssistant, Content="hello"),
        ],
    )

    result = machine.DefaultRuntimeDataChangeApplier().ApplyRuntimeDataChanges(
        original,
        machine.TransitionResult(NextState=machine.StateIdle, RuntimeDataChanges=(machine.ResetConversation(),)),
    )

    assert result.RuntimeData.Messages == [system]


def test_tool_flow_preserves_machine_invariants() -> None:
    """Walk a whole tool round trip and validate after every commit."""
    call = machine.ToolCall(ID="call-1", Name="bash", Input="pwd")
    data = machine.RuntimeData(State=machine.StateWaitingLLM)
    events: tuple[machine.Event, ...] = (
        machine.ToolBatchReceived(Calls=(call,)),
        machine.ToolCallNeedsApproval(Call=call, Request=machine.PermissionRequest(ToolName="bash")),
        machine.ApprovalGranted(Call=call),
        machine.ToolResultReceived(Call=call, Result="ok"),
        machine.ToolBatchFinished(),
    )
    want_states = (
        machine.StateAdvancingQueue,
        machine.StateWaitingApproval,
        machine.StateRunningTool,
        machine.StateAdvancingQueue,
        machine.StateWaitingLLM,
    )

    for index, event in enumerate(events):
        snapshot = machine.SnapshotFrom(data)
        transition = machine.Transition(snapshot, event)
        data = machine.DefaultRuntimeDataChangeApplier().ApplyRuntimeDataChanges(data, transition).RuntimeData
        assert data.State == want_states[index], f"step {index}"


def test_flush_streaming_assistant_appends_only_when_there_is_content() -> None:
    """A cancelled run with nothing streamed must not leave an empty message."""
    applier = machine.DefaultRuntimeDataChangeApplier()
    streaming = machine.RuntimeData(State=machine.StateWaitingLLM, StreamingContent="partial")

    flushed = applier.ApplyRuntimeDataChanges(
        streaming,
        machine.TransitionResult(
            NextState=machine.StateIdle,
            RuntimeDataChanges=(machine.FlushStreamingAssistant(Interrupted=True),),
        ),
    ).RuntimeData
    assert [message.Content for message in flushed.Messages] == ["partial"]
    assert flushed.Messages[0].Interrupted is True

    idle = machine.RuntimeData(State=machine.StateIdle)
    empty = applier.ApplyRuntimeDataChanges(
        idle,
        machine.TransitionResult(
            NextState=machine.StateIdle,
            RuntimeDataChanges=(machine.FlushStreamingAssistant(Interrupted=True),),
        ),
    ).RuntimeData
    assert empty.Messages == []


def test_streaming_chunks_accumulate_and_snapshots_reject_streaming_outside_waiting_llm() -> None:
    applier = machine.DefaultRuntimeDataChangeApplier()
    data = machine.RuntimeData(State=machine.StateWaitingLLM)
    data = applier.ApplyRuntimeDataChanges(
        data,
        machine.TransitionResult(
            NextState=machine.StateWaitingLLM,
            RuntimeDataChanges=(
                machine.AppendStreamingAssistant(Chunk=machine.StreamChunk(ContentDelta="a")),
                machine.AppendStreamingAssistant(Chunk=machine.StreamChunk(ContentDelta="b")),
            ),
        ),
    ).RuntimeData
    assert data.StreamingContent == "ab"

    data.State = machine.StateIdle
    with pytest.raises(machine.InvariantViolationError) as raised:
        machine.SnapshotFrom(data)
    assert raised.value.Reason == "streaming content exists outside WaitingLLM"
