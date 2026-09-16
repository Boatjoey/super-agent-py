"""Go-compatible time formatting.

Timestamps written by this implementation are read by the Go implementation and
the other way round, so the format has to be Go's, not Python's:

* ``time.RFC3339Nano`` drops trailing zeros from the fractional seconds and omits
  the fraction entirely when it is zero, which :func:`strftime` will not do.
* Session identifiers are not timestamps at all: they are ``20060102T150405`` plus
  nine fractional digits, and ``strftime("%f")`` only reaches microseconds, so
  they are formatted from :func:`time.time_ns` by hand.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

#: Go's ``20060102T150405.000000000`` layout, used for session identifiers.
_ID_LAYOUT = "%Y%m%dT%H%M%S"


def FormatRFC3339Nano(moment: datetime) -> str:
    """Format ``moment`` the way Go's ``time.RFC3339Nano`` does."""
    base = f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
    fraction = f"{moment.microsecond:06d}".rstrip("0")
    return f"{base}.{fraction}{_offset(moment)}" if fraction else base + _offset(moment)


def FormatRFC3339(moment: datetime) -> str:
    """Format ``moment`` the way Go's ``time.RFC3339`` does: whole seconds only."""
    base = (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
    )
    return base + _offset(moment)


def NowRFC3339Nano() -> str:
    """The current UTC time, formatted the way Go's telemetry and metadata do."""
    return FormatRFC3339Nano(datetime.now(UTC))


def ParseRFC3339Nano(text: str) -> datetime:
    """Parse a timestamp written by either implementation."""
    return datetime.fromisoformat(text)


def NowID() -> str:
    """A session identifier: UTC, nine fractional digits, without the dot.

    Both implementations derive this from the same clock, so a directory named by
    one is a legal directory name for the other.
    """
    return _formatID(time.time_ns(), keep_dot=False)


def NowTurnID() -> str:
    """A turn identifier: the same instant, but the fraction keeps its dot."""
    return _formatID(time.time_ns(), keep_dot=True)


def _formatID(nanoseconds: int, *, keep_dot: bool) -> str:
    seconds, fraction = divmod(nanoseconds, 1_000_000_000)
    moment = datetime.fromtimestamp(seconds, tz=UTC)
    stamp = moment.strftime(_ID_LAYOUT)
    if keep_dot:
        return f"{stamp}.{fraction:09d}"
    return f"{stamp}{fraction:09d}"


def _offset(moment: datetime) -> str:
    """Go writes UTC as ``Z`` and everything else as ``+HH:MM``."""
    delta: timedelta | None = moment.utcoffset()
    if delta is None:
        return ""
    total = int(delta.total_seconds())
    if total == 0:
        return "Z"
    sign = "+" if total > 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"
