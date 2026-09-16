"""Run identities and the controller that owns the live one.

The controller is what makes a cancelled turn unable to write into the next one:
every action carries the run id it was scheduled under, and a completion whose run
id is no longer current is discarded rather than applied.

Unlike the engine's state lock, these methods are plain synchronous calls. Every
caller runs on the same event loop, and no method awaits, so there is no
interleaving point to protect.
"""

from __future__ import annotations

from typing import Protocol

from super_agent.runtime.protocol.run_context import RunContext


class RunID(str):
    """Identity of one turn's run, as a string-backed type."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"RunID({str.__repr__(self)})"


class ActionID(str):
    """Identity of one queued scheduled action."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"ActionID({str.__repr__(self)})"


class RunController(Protocol):
    """Owns the current run id, its cancellation, and liveness checks."""

    def start_run(self, parent: RunContext) -> tuple[RunID, RunContext]:
        """Cancel the previous run, begin a new one, and return its id and context."""
        ...

    def invalidate_current_run(self) -> None:
        """Cancel the current run and retire its id without starting a new one."""
        ...

    def cancel_run(self) -> None:
        """Cancel the current run and retire its id."""
        ...

    def finish_run(self, run_id: RunID) -> None:
        """End ``run_id`` if it is still the current run."""
        ...

    def current_run_id(self) -> RunID:
        """The current run id, or the zero id when no run is current."""
        ...

    def current_context(self) -> tuple[RunContext | None, bool]:
        """The current run's context, and whether one exists."""
        ...

    def is_current(self, run_id: RunID) -> bool:
        """Whether ``run_id`` is the live run."""
        ...


class DefaultRunController:
    """The only controller; the port exists so tests can substitute one."""

    __slots__ = ("_cancel", "_context", "_next", "_run_id")

    def __init__(self) -> None:
        self._next = 0
        self._run_id = RunID("")
        self._context: RunContext | None = None

    def start_run(self, parent: RunContext) -> tuple[RunID, RunContext]:
        self._cancel_current()
        self._next += 1
        self._run_id = RunID(f"run-{self._next}")
        self._context = RunContext(parent)
        return self._run_id, self._context

    def invalidate_current_run(self) -> None:
        self._cancel_current()
        self._next += 1
        self._run_id = RunID(f"run-{self._next}")

    def cancel_run(self) -> None:
        self._cancel_current()
        self._next += 1
        self._run_id = RunID(f"run-{self._next}")

    def finish_run(self, run_id: RunID) -> None:
        if run_id == "" or run_id != self._run_id:
            return
        self._cancel_current()

    def current_run_id(self) -> RunID:
        return self._run_id

    def current_context(self) -> tuple[RunContext | None, bool]:
        if self._context is None:
            return None, False
        return self._context, True

    def is_current(self, run_id: RunID) -> bool:
        return run_id != "" and run_id == self._run_id

    def _cancel_current(self) -> None:
        if self._context is not None:
            self._context.cancel()
            self._context = None
