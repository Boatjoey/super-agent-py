"""Batch dispatch through the unified approval events.

The other case asserts on ``EngineView`` batch progress and lives in
``tests/runtime/test_engine.py``.
"""

from __future__ import annotations

from super_agent.runtime import machine
from tests.runtime.test_transition import transition_snapshot

CALLS = (
    machine.ToolCall(ID="call-1", Name="bash", Input="one"),
    machine.ToolCall(ID="call-2", Name="bash", Input="two"),
)


def test_tool_batch_received_advances_through_unified_approval_events() -> None:
    start_event = machine.ToolBatchReceived(Content="need tools", Calls=CALLS)
    start = machine.Transition(transition_snapshot(machine.StateWaitingLLM, start_event), start_event)
    assert start.NextState == machine.StateAdvancingQueue
    assert len(start.ActionPlan.Schedule) == 1
    assert isinstance(start.ActionPlan.Schedule[0], machine.CheckToolQueue)

    first_event = machine.ToolCallNeedsApproval(Call=CALLS[0])
    first = machine.Transition(transition_snapshot(machine.StateAdvancingQueue, first_event), first_event)
    assert first.NextState == machine.StateWaitingApproval

    second_event = machine.ToolCallNeedsApproval(Call=CALLS[1])
    second = machine.Transition(transition_snapshot(machine.StateAdvancingQueue, second_event), second_event)
    assert second.NextState == machine.StateWaitingApproval


def test_snapshot_includes_pending_tool_batch_progress() -> None:
    """The header shows "2/2", so the batch counters must reach the view.

    Building the view literal only pins the field names. The second half here also
    drives the real ``Snapshot()`` path, because that is where the counters are
    actually filled in — and where a regression would hide.
    """
    from super_agent.runtime.engine import EngineView

    batch = machine.ToolCallBatch(ID="batch-1", Calls=list(CALLS), Index=2)
    view = EngineView(
        PendingTool=batch.Calls[1],
        PendingToolBatchID=batch.ID,
        PendingToolBatchIndex=batch.Index,
        PendingToolBatchTotal=len(batch.Calls),
    )
    assert view.PendingToolBatchIndex == 2
    assert view.PendingToolBatchTotal == 2

    from super_agent.runtime.engine import NewEngine

    engine = NewEngine(None, None, None)
    # Seeding the committed data directly is the point: Snapshot is the subject.
    engine._runtime_data = machine.RuntimeData(  # pyright: ignore[reportPrivateUsage]
        State=machine.StateWaitingApproval,
        PendingTool=batch.Calls[1],
        PendingPermission=machine.PermissionRequest(ToolName="bash"),
        ToolBatch=batch,
    )
    live = engine.Snapshot()
    assert live.PendingToolBatchID == "batch-1"
    assert live.PendingToolBatchIndex == 2
    assert live.PendingToolBatchTotal == 2

    # Outside an awaiting-approval state the counters stay empty: there is no
    # batch to show progress for, which is why the header omits them.
    idle = NewEngine(None, None, None).Snapshot()
    assert idle.PendingToolBatchIndex == 0
    assert idle.PendingToolBatchTotal == 0


def test_batch_id_is_derived_from_the_first_call() -> None:
    """The batch identity is what the TUI shows as progress, so it must be stable."""
    event = machine.ToolBatchReceived(Calls=CALLS)
    result = machine.Transition(transition_snapshot(machine.StateWaitingLLM, event), event)
    batch_change = result.RuntimeDataChanges[1]
    assert isinstance(batch_change, machine.SetToolCallBatch)
    assert batch_change.ID == "batch-call-1"
    assert batch_change.Calls == CALLS

    anonymous = machine.ToolBatchReceived(Calls=(machine.ToolCall(Name="bash", Input="one"),))
    result = machine.Transition(transition_snapshot(machine.StateWaitingLLM, anonymous), anonymous)
    batch_change = result.RuntimeDataChanges[1]
    assert isinstance(batch_change, machine.SetToolCallBatch)
    assert batch_change.ID == "batch"
