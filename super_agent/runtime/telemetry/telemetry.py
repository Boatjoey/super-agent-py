"""One process-wide JSONL sink for run, action, and transition records.

The sink is configured once at startup from ``settings.json``'s
``telemetry.log_path``. Recording is best effort: a missing sink, an unserialisable
field, or a failed write all mean the record is dropped, never that the run fails.

The lock is a :class:`threading.Lock`, not an :class:`asyncio.Lock`, because
:func:`Record` is called from synchronous paths — the stream-chunk commit runs
inside a model callback, not inside a coroutine.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Mapping
from typing import Any, Final

from super_agent import jsonutil
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.timeutil import now_rfc3339_nano

#: One record's fields.
Fields = Mapping[str, Any]

_lock = threading.Lock()
_fd: int | None = None


def configure(path: str) -> None:
    """Point the sink at ``path``, or disable it when ``path`` is empty.

    Configuring twice is allowed; the previous file is closed first.
    """
    global _fd
    with _lock:
        _close_locked()
        if path == "":
            return
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, mode=0o700, exist_ok=True)
        _fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)


def close() -> None:
    """Close the sink. Idempotent."""
    global _fd
    with _lock:
        _close_locked()


def is_configured() -> bool:
    """Whether a sink is open. Used by tests to assert a clean fixture."""
    with _lock:
        return _fd is not None


def record(kind: str, fields: Fields) -> None:
    """Append one record, stamped with the current UTC time and ``kind``.

    A field named ``time`` or ``kind`` overwrites the stamp.
    """
    with _lock:
        if _fd is None:
            return
        record: dict[str, Any] = {"time": now_rfc3339_nano(), "kind": kind}
        record.update(fields)
        try:
            line = jsonutil.dumps(record)
        except (TypeError, ValueError):
            return
        try:
            # Written straight to the descriptor, unbuffered, so a reader that
            # opens the file after this call sees the record.
            os.write(_fd, (line + "\n").encode())
        except OSError:
            return


def _close_locked() -> None:
    global _fd
    if _fd is None:
        return
    with contextlib.suppress(OSError):
        os.close(_fd)
    _fd = None


class IDs:
    """The run and action a record belongs to."""

    __slots__ = ("action_id", "run_id")

    def __init__(self, run_id: str = "", action_id: str = "") -> None:
        self.run_id = run_id
        self.action_id = action_id

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, IDs):
            return NotImplemented
        return self.run_id == other.run_id and self.action_id == other.action_id

    def __repr__(self) -> str:
        return f"IDs(run_id={self.run_id!r}, action_id={self.action_id!r})"


_IDS_KEY: Final[object] = object()


def with_i_ds(ctx: RunContext, run_id: str, action_id: str) -> RunContext:
    """A context derived from ``ctx`` carrying the run and action identity.

    The derived context shares ``ctx``'s cancellation, so waiting on either
    observes the same cancel.
    """
    return ctx.with_value(_IDS_KEY, IDs(run_id=run_id, action_id=action_id))


def i_ds_from(ctx: RunContext) -> IDs:
    """The identity attached to ``ctx``, or an empty one."""
    ids = ctx.value(_IDS_KEY)
    return ids if isinstance(ids, IDs) else IDs()
