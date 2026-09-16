"""The six runtime phases.

``State`` is string-backed because the value is written into snapshots and
diagnostics and has to serialise as a bare string.
"""

from __future__ import annotations

from typing import Final


class State(str):
    """The current runtime phase."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"State({str.__repr__(self)})"


STATE_INITIALIZING: Final[State] = State("Initializing")
STATE_IDLE: Final[State] = State("Idle")
STATE_WAITING_LLM: Final[State] = State("WaitingLLM")
STATE_WAITING_APPROVAL: Final[State] = State("WaitingApproval")
STATE_RUNNING_TOOL: Final[State] = State("RunningTool")
STATE_ADVANCING_QUEUE: Final[State] = State("AdvancingQueue")

#: Every state, in declaration order.
ALL_STATES: Final[tuple[State, ...]] = (
    STATE_INITIALIZING,
    STATE_IDLE,
    STATE_WAITING_LLM,
    STATE_WAITING_APPROVAL,
    STATE_RUNNING_TOOL,
    STATE_ADVANCING_QUEUE,
)

#: The zero value of the type, used as the registry key for events that every
#: state accepts. Not a legal running state; see ``transition.py``.
ZERO_STATE: Final[State] = State("")
