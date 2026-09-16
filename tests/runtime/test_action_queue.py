"""Queue identity and ordering."""

from __future__ import annotations

from super_agent.runtime import machine
from super_agent.runtime.execution import ActionQueue, RunID


def test_action_queue_queues_scheduled_actions_with_run_and_incrementing_ids() -> None:
    queue = ActionQueue()

    first = queue.Queue(RunID("run-1"), machine.CallModel())
    second = queue.Queue(RunID("run-1"), machine.CheckToolQueue())

    assert first.RunID == "run-1"
    assert first.ActionID == "action-1"
    assert second.ActionID == "action-2"
    assert queue.Len() == 2


def test_action_queue_pops_in_fifo_order() -> None:
    queue = ActionQueue()
    first = queue.Queue(RunID("run-1"), machine.CallModel())
    second = queue.Queue(RunID("run-1"), machine.CheckToolQueue())

    assert queue.Pop() == first
    assert queue.Pop() == second
    assert queue.Pop() is None


def test_action_queue_clear_drops_pending_scheduled_actions() -> None:
    queue = ActionQueue()
    queue.Queue(RunID("run-1"), machine.CallModel())
    queue.Queue(RunID("run-1"), machine.CheckToolQueue())

    queue.Clear()

    assert queue.Len() == 0
    assert queue.Pop() is None


def test_action_ids_stay_unique_across_a_clear() -> None:
    """A cleared action must not be confusable with a later one.

    The engine drops stale completions by comparing action ids, so reusing an id
    after ``Clear`` would let a cancelled action's result apply to its successor.
    """
    queue = ActionQueue()
    queue.Queue(RunID("run-1"), machine.CallModel())
    queue.Clear()

    queued = queue.Queue(RunID("run-1"), machine.CallModel())

    assert queued.ActionID == "action-2"
