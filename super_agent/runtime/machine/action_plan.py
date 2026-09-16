"""How a committed transition updates future work."""

from __future__ import annotations

import dataclasses

from super_agent.runtime.machine.scheduled_action import ScheduledAction


@dataclasses.dataclass(frozen=True, slots=True)
class ActionPlan:
    """Clearing obsolete queue work and scheduling new work, as one decision.

    The two are not separate: the engine commits the plan together with the
    runtime data under a single lock and only then runs the scheduled actions, so
    there is no window in which the queue belongs to a state that no longer
    exists.
    """

    ClearExisting: bool = False
    Schedule: tuple[ScheduledAction, ...] = ()
