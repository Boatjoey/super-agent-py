"""Three error types for three different mistakes."""

from __future__ import annotations

from super_agent.runtime.machine.event import Event
from super_agent.runtime.machine.state import State


def _event_name(event: Event | None) -> str:
    """The event's type name, or ``"<nil>"`` for a missing event."""
    return "<nil>" if event is None else type(event).__name__


class UnexpectedEventError(Exception):
    """The current state does not accept this event."""

    def __init__(self, state: State, event: Event | None) -> None:
        self.State = state
        self.Event = event
        super().__init__(f"state {state} does not accept event {_event_name(event)}")


class ProtocolViolationError(Exception):
    """The event type is valid here, but its content does not match current data.

    A mismatched call ID, a batch that finished before its queue drained, or an
    empty tool batch all land here.
    """

    def __init__(self, state: State, event: Event | None, reason: str) -> None:
        self.State = state
        self.Event = event
        self.Reason = reason
        super().__init__(f"protocol violation in state {state} for event {_event_name(event)}: {reason}")


class InvariantViolationError(Exception):
    """``RuntimeData`` itself is impossible."""

    def __init__(self, reason: str) -> None:
        self.Reason = reason
        super().__init__("engine state invariant violation: " + reason)
