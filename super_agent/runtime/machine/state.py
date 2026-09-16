"""The six runtime phases.

``State`` is string-backed, exactly like Go's, because the value is written into
snapshots and diagnostics and has to read the same in both implementations.
"""

from __future__ import annotations

from typing import Final


class State(str):
    """The current runtime phase."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"State({str.__repr__(self)})"


StateInitializing: Final[State] = State("Initializing")
StateIdle: Final[State] = State("Idle")
StateWaitingLLM: Final[State] = State("WaitingLLM")
StateWaitingApproval: Final[State] = State("WaitingApproval")
StateRunningTool: Final[State] = State("RunningTool")
StateAdvancingQueue: Final[State] = State("AdvancingQueue")

#: Every state, in declaration order.
AllStates: Final[tuple[State, ...]] = (
    StateInitializing,
    StateIdle,
    StateWaitingLLM,
    StateWaitingApproval,
    StateRunningTool,
    StateAdvancingQueue,
)

#: The zero value of the type, used as the registry key for events that every
#: state accepts. Not a legal running state; see ``transition.py``.
ZeroState: Final[State] = State("")
