"""Batch dispatch through the unified approval events.

The other case asserts on ``EngineView`` batch progress and lives in
``tests/runtime/test_engine.py``.
"""

from __future__ import annotations

from super_agent.runtime import machine
from tests.runtime.test_transition import transition_snapshot

CALLS = (
    machine.ToolCall(id="call-1", name="bash", input="one"),
    machine.ToolCall(id="call-2", name="bash", input="two"),
)


def test_tool_batch_received_advances_through_unified_approval_events() -> None:
    start_event = machine.ToolBatchReceived(content="need tools", calls=CALLS)
    start = machine.transition(transition_snapshot(machine.STATE_WAITING_LLM, start_event), start_event)
    assert start.next_state == machine.STATE_ADVANCING_QUEUE
    assert len(start.action_plan.schedule) == 1
    assert isinstance(start.action_plan.schedule[0], machine.CheckToolQueue)

    first_event = machine.ToolCallNeedsApproval(call=CALLS[0])
    first = machine.transition(transition_snapshot(machine.STATE_ADVANCING_QUEUE, first_event), first_event)
    assert first.next_state == machine.STATE_WAITING_APPROVAL

    second_event = machine.ToolCallNeedsApproval(call=CALLS[1])
    second = machine.transition(transition_snapshot(machine.STATE_ADVANCING_QUEUE, second_event), second_event)
    assert second.next_state == machine.STATE_WAITING_APPROVAL


def test_snapshot_includes_pending_tool_batch_progress() -> None:
    """The header shows "2/2", so the batch counters must reach the view.

    Building the view literal only pins the field names. The second half here also
    drives the real ``Snapshot()`` path, because that is where the counters are
    actually filled in — and where a regression would hide.
    """
    from super_agent.runtime.engine import EngineView

    batch = machine.ToolCallBatch(id="batch-1", calls=list(CALLS), index=2)
    view = EngineView(
        pending_tool=batch.calls[1],
        pending_tool_batch_id=batch.id,
        pending_tool_batch_index=batch.index,
        pending_tool_batch_total=len(batch.calls),
    )
    assert view.pending_tool_batch_index == 2
    assert view.pending_tool_batch_total == 2

    from super_agent.runtime.engine import new_engine

    engine = new_engine(None, None, None)
    # Seeding the committed data directly is the point: Snapshot is the subject.
    engine._runtime_data = machine.RuntimeData(  # pyright: ignore[reportPrivateUsage]
        state=machine.STATE_WAITING_APPROVAL,
        pending_tool=batch.calls[1],
        pending_permission=machine.PermissionRequest(tool_name="bash"),
        tool_batch=batch,
    )
    live = engine.snapshot()
    assert live.pending_tool_batch_id == "batch-1"
    assert live.pending_tool_batch_index == 2
    assert live.pending_tool_batch_total == 2

    # Outside an awaiting-approval state the counters stay empty: there is no
    # batch to show progress for, which is why the header omits them.
    idle = new_engine(None, None, None).snapshot()
    assert idle.pending_tool_batch_index == 0
    assert idle.pending_tool_batch_total == 0


def test_batch_id_is_derived_from_the_first_call() -> None:
    """The batch identity is what the TUI shows as progress, so it must be stable."""
    event = machine.ToolBatchReceived(calls=CALLS)
    result = machine.transition(transition_snapshot(machine.STATE_WAITING_LLM, event), event)
    batch_change = result.runtime_data_changes[1]
    assert isinstance(batch_change, machine.SetToolCallBatch)
    assert batch_change.id == "batch-call-1"
    assert batch_change.calls == CALLS

    anonymous = machine.ToolBatchReceived(calls=(machine.ToolCall(name="bash", input="one"),))
    result = machine.transition(transition_snapshot(machine.STATE_WAITING_LLM, anonymous), anonymous)
    batch_change = result.runtime_data_changes[1]
    assert isinstance(batch_change, machine.SetToolCallBatch)
    assert batch_change.id == "batch"
