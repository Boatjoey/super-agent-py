"""Timestamp formatting shared across the on-disk artefacts.

The formats are a persisted contract, so they are written by hand rather than
left to Python's defaults:

* RFC3339Nano drops trailing zeros from the fractional seconds and omits the
  fraction entirely when it is zero, which :func:`strftime` will not do.
* Session identifiers are not timestamps at all: they are ``20060102T150405`` plus
  nine fractional digits, and ``strftime("%f")`` only reaches microseconds, so
  they are formatted from :func:`time.time_ns` by hand.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

#: The ``20060102T150405`` layout, used for session identifiers.
_ID_LAYOUT = "%Y%m%dT%H%M%S"


def format_rfc3339_nano(moment: datetime) -> str:
    """Format ``moment`` as RFC3339Nano."""
    base = f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
    fraction = f"{moment.microsecond:06d}".rstrip("0")
    return f"{base}.{fraction}{_offset(moment)}" if fraction else base + _offset(moment)


def format_rfc3339(moment: datetime) -> str:
    """Format ``moment`` as RFC3339: whole seconds only."""
    base = (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
    )
    return base + _offset(moment)


def now_rfc3339_nano() -> str:
    """The current UTC time, formatted as the telemetry and metadata records are."""
    return format_rfc3339_nano(datetime.now(UTC))


def parse_rfc3339_nano(text: str) -> datetime:
    """Parse a stored RFC3339 timestamp."""
    return datetime.fromisoformat(text)


def now_id() -> str:
    """A session identifier: UTC, nine fractional digits, without the dot.

    The format is fixed, so every identifier stays a legal directory name.
    """
    return _formatID(time.time_ns(), keep_dot=False)


def now_turn_id() -> str:
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
    """UTC is written as ``Z`` and every other offset as ``+HH:MM``."""
    delta: timedelta | None = moment.utcoffset()
    if delta is None:
        return ""
    total = int(delta.total_seconds())
    if total == 0:
        return "Z"
    sign = "+" if total > 0 else "-"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"
