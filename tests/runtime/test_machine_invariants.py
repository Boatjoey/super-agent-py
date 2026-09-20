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
        "AdvancingQueue-without-batch": machine.RuntimeData(state=machine.STATE_ADVANCING_QUEUE),
        "WaitingApproval-without-approval": machine.RuntimeData(state=machine.STATE_WAITING_APPROVAL),
        "RunningTool-without-tool": machine.RuntimeData(state=machine.STATE_RUNNING_TOOL),
        "Idle-with-a-batch": machine.RuntimeData(state=machine.STATE_IDLE, tool_batch=machine.ToolCallBatch()),
    }
    for label, data in cases.items():
        with pytest.raises(machine.InvariantViolationError):
            machine.snapshot_from(data)
        assert label  # the label is the case identity in the failure message


def test_transition_classifies_state_mismatch_as_unexpected_event() -> None:
    event = machine.ToolResultReceived(call=machine.ToolCall(id="call-1"), result="ok")
    with pytest.raises(machine.UnexpectedEventError):
        machine.transition(transition_snapshot(machine.STATE_IDLE, event), event)


def test_transition_rejects_approval_for_different_call() -> None:
    pending = machine.ToolCall(id="call-1", name="bash", input="pwd")
    data = machine.RuntimeData(
        state=machine.STATE_WAITING_APPROVAL,
        pending_tool=pending,
        pending_permission=machine.PermissionRequest(),
        tool_batch=machine.ToolCallBatch(calls=[pending], index=1),
    )
    snapshot = machine.snapshot_from(data)
    with pytest.raises(machine.ProtocolViolationError) as raised:
        machine.transition(
            snapshot, machine.ApprovalGranted(call=machine.ToolCall(id="call-2", name="bash", input="pwd"))
        )
    assert raised.value.reason == "approved call does not match pending tool"


def test_transition_rejects_result_for_different_current_call() -> None:
    current = machine.ToolCall(id="call-1", name="bash", input="pwd")
    data = machine.RuntimeData(
        state=machine.STATE_RUNNING_TOOL,
        current_tool=current,
        tool_batch=machine.ToolCallBatch(calls=[current], index=1),
    )
    snapshot = machine.snapshot_from(data)
    with pytest.raises(machine.ProtocolViolationError) as raised:
        machine.transition(snapshot, machine.ToolResultReceived(call=machine.ToolCall(id="call-2"), result="ok"))
    assert raised.value.reason == "result call does not match current tool"


def test_transition_rejects_batch_finished_before_queue_empty() -> None:
    call = machine.ToolCall(id="call-1")
    data = machine.RuntimeData(state=machine.STATE_ADVANCING_QUEUE, tool_batch=machine.ToolCallBatch(calls=[call]))
    snapshot = machine.snapshot_from(data)
    with pytest.raises(machine.ProtocolViolationError) as raised:
        machine.transition(snapshot, machine.ToolBatchFinished())
    assert raised.value.reason == "tool batch finished before the queue was empty"


def test_transition_rejects_an_empty_tool_batch() -> None:
    """An empty batch is a protocol violation, not a batch nobody queued."""
    snapshot = transition_snapshot(machine.STATE_WAITING_LLM, machine.EngineReady())
    with pytest.raises(machine.ProtocolViolationError) as raised:
        machine.transition(snapshot, machine.ToolBatchReceived(calls=()))
    assert raised.value.reason == "tool batch is empty"


def test_runtime_data_change_applier_does_not_mutate_original_state_when_validation_fails() -> None:
    """A rejected candidate leaves the committed data untouched."""
    call = machine.ToolCall(id="call-1")
    original = machine.RuntimeData(state=machine.STATE_ADVANCING_QUEUE, tool_batch=machine.ToolCallBatch(calls=[call]))

    with pytest.raises(machine.InvariantViolationError):
        machine.DefaultRuntimeDataChangeApplier().apply_runtime_data_changes(
            original,
            machine.TransitionResult(
                next_state=machine.STATE_RUNNING_TOOL,
                runtime_data_changes=(machine.AdvanceToolCallBatch(),),
            ),
        )

    assert original.state == machine.STATE_ADVANCING_QUEUE
    assert original.tool_batch is not None
    assert original.tool_batch.index == 0
    assert original.current_tool is None


def test_applier_raises_invariant_error_for_a_change_it_cannot_apply() -> None:
    with pytest.raises(machine.InvariantViolationError) as raised:
        machine.DefaultRuntimeDataChangeApplier().apply_runtime_data_changes(
            machine.RuntimeData(state=machine.STATE_IDLE),
            machine.TransitionResult(
                next_state=machine.STATE_IDLE,
                runtime_data_changes=(machine.AdvanceToolCallBatch(),),
            ),
        )
    assert raised.value.reason == "cannot advance an empty tool batch"


def test_applier_raises_invariant_error_for_an_unknown_change() -> None:
    """The applier's last line of defence: a change it does not recognise."""
    rogue = cast("machine.RuntimeDataChange", object())
    with pytest.raises(machine.InvariantViolationError) as raised:
        machine.DefaultRuntimeDataChangeApplier().apply_runtime_data_changes(
            machine.RuntimeData(state=machine.STATE_IDLE),
            machine.TransitionResult(next_state=machine.STATE_IDLE, runtime_data_changes=(rogue,)),
        )
    assert raised.value.reason == "unknown runtime data change object"


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
    batch = machine.ToolCallBatch(calls=[sample_tool_call()], index=0)
    original = machine.RuntimeData(state=machine.STATE_ADVANCING_QUEUE, tool_batch=batch)

    cloned = machine.clone_runtime_data(original)
    assert cloned.tool_batch is not None
    cloned.tool_batch.index = 1

    assert original.tool_batch is not None
    assert original.tool_batch.index == 0


def test_reset_conversation_preserves_system_messages() -> None:
    """Project instructions must survive a reset, and replay applies the same rule."""
    system = machine.Message(role=machine.ROLE_SYSTEM, content="instructions")
    original = machine.RuntimeData(
        state=machine.STATE_IDLE,
        messages=[
            system,
            machine.Message(role=machine.ROLE_USER, content="hi"),
            machine.Message(role=machine.ROLE_ASSISTANT, content="hello"),
        ],
    )

    result = machine.DefaultRuntimeDataChangeApplier().apply_runtime_data_changes(
        original,
        machine.TransitionResult(next_state=machine.STATE_IDLE, runtime_data_changes=(machine.ResetConversation(),)),
    )

    assert result.runtime_data.messages == [system]


def test_tool_flow_preserves_machine_invariants() -> None:
    """Walk a whole tool round trip and validate after every commit."""
    call = machine.ToolCall(id="call-1", name="bash", input="pwd")
    data = machine.RuntimeData(state=machine.STATE_WAITING_LLM)
    events: tuple[machine.Event, ...] = (
        machine.ToolBatchReceived(calls=(call,)),
        machine.ToolCallNeedsApproval(call=call, request=machine.PermissionRequest(tool_name="bash")),
        machine.ApprovalGranted(call=call),
        machine.ToolResultReceived(call=call, result="ok"),
        machine.ToolBatchFinished(),
    )
    want_states = (
        machine.STATE_ADVANCING_QUEUE,
        machine.STATE_WAITING_APPROVAL,
        machine.STATE_RUNNING_TOOL,
        machine.STATE_ADVANCING_QUEUE,
        machine.STATE_WAITING_LLM,
    )

    for index, event in enumerate(events):
        snapshot = machine.snapshot_from(data)
        transition = machine.transition(snapshot, event)
        data = machine.DefaultRuntimeDataChangeApplier().apply_runtime_data_changes(data, transition).runtime_data
        assert data.state == want_states[index], f"step {index}"


def test_flush_streaming_assistant_appends_only_when_there_is_content() -> None:
    """A cancelled run with nothing streamed must not leave an empty message."""
    applier = machine.DefaultRuntimeDataChangeApplier()
    streaming = machine.RuntimeData(state=machine.STATE_WAITING_LLM, streaming_content="partial")

    flushed = applier.apply_runtime_data_changes(
        streaming,
        machine.TransitionResult(
            next_state=machine.STATE_IDLE,
            runtime_data_changes=(machine.FlushStreamingAssistant(interrupted=True),),
        ),
    ).runtime_data
    assert [message.content for message in flushed.messages] == ["partial"]
    assert flushed.messages[0].interrupted is True

    idle = machine.RuntimeData(state=machine.STATE_IDLE)
    empty = applier.apply_runtime_data_changes(
        idle,
        machine.TransitionResult(
            next_state=machine.STATE_IDLE,
            runtime_data_changes=(machine.FlushStreamingAssistant(interrupted=True),),
        ),
    ).runtime_data
    assert empty.messages == []


def test_streaming_change_reuses_unchanged_message_history() -> None:
    """A stream chunk must not copy a long transcript it cannot mutate."""
    data = machine.RuntimeData(
        state=machine.STATE_WAITING_LLM,
        messages=[machine.Message(role=machine.ROLE_USER, content="history")],
    )

    changed = machine.DefaultRuntimeDataChangeApplier().apply_runtime_data_changes(
        data,
        machine.TransitionResult(
            next_state=machine.STATE_WAITING_LLM,
            runtime_data_changes=(machine.AppendStreamingAssistant(chunk=machine.StreamChunk(content_delta="piece")),),
        ),
    )

    assert changed.runtime_data.messages is data.messages
    assert changed.runtime_data.streaming_content == "piece"


def test_streaming_chunks_accumulate_and_snapshots_reject_streaming_outside_waiting_llm() -> None:
    applier = machine.DefaultRuntimeDataChangeApplier()
    data = machine.RuntimeData(state=machine.STATE_WAITING_LLM)
    data = applier.apply_runtime_data_changes(
        data,
        machine.TransitionResult(
            next_state=machine.STATE_WAITING_LLM,
            runtime_data_changes=(
                machine.AppendStreamingAssistant(chunk=machine.StreamChunk(content_delta="a")),
                machine.AppendStreamingAssistant(chunk=machine.StreamChunk(content_delta="b")),
            ),
        ),
    ).runtime_data
    assert data.streaming_content == "ab"

    data.state = machine.STATE_IDLE
    with pytest.raises(machine.InvariantViolationError) as raised:
        machine.snapshot_from(data)
    assert raised.value.reason == "streaming content exists outside WaitingLLM"
