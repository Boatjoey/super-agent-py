"""The FIFO of work the agent loop drains.

This is a plain list with a monotonic counter, not an :class:`asyncio.Queue`. The
engine only ever touches it while holding the engine lock and never awaits inside
that critical section, so an asyncio primitive would add failure modes — a
cancelled ``get()`` swallowing an item, a ``maxsize`` making ``put`` a yield
point — without adding any synchronisation.
"""

from __future__ import annotations

from super_agent.runtime.execution.run_controller import ActionID, RunID
from super_agent.runtime.execution.scheduled_action_runner import QueuedAction
from super_agent.runtime.machine.scheduled_action import ScheduledAction


class ActionQueue:
    """A FIFO of queued actions, each stamped with its run and action identity."""

    __slots__ = ("_next", "_queue")

    def __init__(self) -> None:
        self._queue: list[QueuedAction] = []
        self._next = 0

    def queue(self, run_id: RunID, action: ScheduledAction) -> QueuedAction:
        """Append ``action`` under ``run_id`` and return the stamped entry."""
        self._next += 1
        queued = QueuedAction(run_id=run_id, action_id=ActionID(f"action-{self._next}"), action=action)
        self._queue.append(queued)
        return queued

    def pop(self) -> QueuedAction | None:
        """Remove and return the oldest entry, or ``None`` when empty."""
        if not self._queue:
            return None
        return self._queue.pop(0)

    def clear(self) -> None:
        """Drop every queued entry.

        The counter is not reset: action ids stay unique for the life of the
        engine, so a late completion for a cleared action can never be mistaken
        for one belonging to a later action.
        """
        self._queue = []

    def len(self) -> int:
        return len(self._queue)
