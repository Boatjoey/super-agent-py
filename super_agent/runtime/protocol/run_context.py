"""Cancellation for one run.

Only cancellation is modelled as an object here: deadlines are expressed with
``asyncio.wait_for`` and values with :mod:`contextvars`, so a ``RunContext`` only
has to answer "has this run been cancelled" and to give waiters something to
block on.

It lives in ``runtime/protocol`` because it appears in the port signatures
(``Model.next``, ``ToolRunner.run``) and because ``llm`` and ``tools`` may reach
only this package under the dependency rule.
"""

from __future__ import annotations

import asyncio

from super_agent.errors import Cancelled

#: Reason attached to a :class:`Cancelled` raised by :meth:`RunContext.raise_if_cancelled`.
DEFAULT_CANCEL_REASON = "run cancelled"


class RunContext:
    """Cancellation for one run.

    ``cancelled`` is an :class:`asyncio.Event` rather than a channel because the
    engines and adapters here need "wait until cancelled or until something else
    happens", which ``asyncio.wait`` expresses directly.

    A context may be derived from a parent. Cancelling the parent cancels every
    context derived from it, which is the part of the context tree the run
    controller relies on: a turn's context is derived from the caller's, so
    cancelling the caller stops the run.
    """

    __slots__ = ("_children", "_values", "cancelled")

    def __init__(self, parent: RunContext | None = None) -> None:
        self.cancelled: asyncio.Event = asyncio.Event()
        self._children: list[RunContext] = []
        self._values: dict[object, object] = {}
        if parent is not None:
            parent.adopt(self)

    def with_value(self, key: object, value: object) -> RunContext:
        """A context derived from this one that also carries ``key``.

        The derived context *shares* the cancellation event rather than
        registering as a child. A parent chain would be safe because it is
        collected with the value; here the parent holds its children, so
        registering a per-action context would leak one entry per action for the
        life of the run.
        """
        derived = RunContext()
        derived.cancelled = self.cancelled
        derived._values = {**self._values, key: value}
        return derived

    def value(self, key: object) -> object | None:
        """The value attached to ``key``, or ``None``."""
        return self._values.get(key)

    def adopt(self, child: RunContext) -> None:
        """Make ``child`` follow this context's cancellation."""
        if self.cancelled.is_set():
            child.cancel()
            return
        self._children.append(child)

    def done(self) -> asyncio.Event:
        """The event to wait on for cancellation."""
        return self.cancelled

    def err(self) -> Exception | None:
        """``None`` while the run is live, a :class:`Cancelled` once it is not."""
        return Cancelled(DEFAULT_CANCEL_REASON) if self.cancelled.is_set() else None

    def cancel(self) -> None:
        """Cancel the run and everything derived from it.

        Idempotent, like closing a channel once: a second call does not re-notify
        children, so a cancelled subtree is walked at most once.
        """
        if self.cancelled.is_set():
            return
        self.cancelled.set()
        for child in self._children:
            child.cancel()

    def raise_if_cancelled(self) -> None:
        """Raise :class:`Cancelled` when the run has already been cancelled."""
        if self.cancelled.is_set():
            raise Cancelled(DEFAULT_CANCEL_REASON)

    def __repr__(self) -> str:
        return f"RunContext(cancelled={self.cancelled.is_set()})"


def live_context() -> RunContext:
    """A context that is never cancelled by itself, for helpers outside a run.

    The engine uses this for the commands that are not part of a turn.
    """
    return RunContext()
