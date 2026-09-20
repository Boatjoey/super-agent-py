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

import re
import time
from datetime import UTC, datetime, timedelta
from typing import Final

#: The ``20060102T150405`` layout, used for session identifiers.
_ID_LAYOUT = "%Y%m%dT%H%M%S"

#: The fractional seconds of a stored stamp, as written after the decimal point.
_FRACTION: Final[re.Pattern[str]] = re.compile(r"\.(\d+)")

_NANOS_PER_SECOND: Final[int] = 1_000_000_000
_NANOS_PER_MICROSECOND: Final[int] = 1_000


class Timestamp(datetime):
    """An instant that keeps the nanoseconds :class:`datetime` cannot hold.

    The persisted format carries nine fractional digits and :class:`datetime`
    carries microseconds, so a parsed stamp keeps the remaining digits beside
    its microseconds: reading an artefact and writing it back is lossless. The
    microseconds stay the value comparisons and arithmetic use.
    """

    __slots__ = ("_nanoseconds",)

    @property
    def nanoseconds(self) -> int:
        """Fractional seconds, to nanosecond resolution."""
        kept = getattr(self, "_nanoseconds", None)
        return self.microsecond * _NANOS_PER_MICROSECOND if kept is None else kept

    @classmethod
    def from_moment(cls, moment: datetime, nanoseconds: int) -> Timestamp:
        """``moment``, keeping ``nanoseconds`` as its fractional seconds."""
        stamp = cls(
            moment.year,
            moment.month,
            moment.day,
            moment.hour,
            moment.minute,
            moment.second,
            moment.microsecond,
            tzinfo=moment.tzinfo,
            fold=moment.fold,
        )
        stamp._nanoseconds = nanoseconds
        return stamp


def format_rfc3339_nano(moment: datetime) -> str:
    """Format ``moment`` as RFC3339Nano."""
    base = f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
    fraction = f"{_nanoseconds(moment):09d}".rstrip("0")
    return f"{base}.{fraction}{_offset(moment)}" if fraction else base + _offset(moment)


def format_rfc3339(moment: datetime) -> str:
    """Format ``moment`` as RFC3339: whole seconds only."""
    base = (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
    )
    return base + _offset(moment)


def format_id(moment: Timestamp, *, keep_dot: bool) -> str:
    """A session or turn identifier: ``%Y%m%dT%H%M%S`` plus nine fractional digits.

    The stamp is UTC, so the identifier names an instant rather than a wall
    clock, and ``keep_dot`` writes the dot before the fraction; both spellings
    are legal directory names.
    """
    stamp = moment.astimezone(UTC).strftime(_ID_LAYOUT)
    fraction = f"{moment.nanoseconds:09d}"
    return f"{stamp}.{fraction}" if keep_dot else f"{stamp}{fraction}"


def now() -> Timestamp:
    """The current UTC instant, from the nanosecond clock."""
    seconds, nanoseconds = divmod(time.time_ns(), _NANOS_PER_SECOND)
    moment = datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=nanoseconds // _NANOS_PER_MICROSECOND)
    return Timestamp.from_moment(moment, nanoseconds)


def now_rfc3339_nano() -> str:
    """The current UTC time, formatted as the telemetry and metadata records are."""
    return format_rfc3339_nano(now())


def parse_rfc3339_nano(text: str) -> datetime:
    """Parse a stored RFC3339 timestamp, keeping all nine fractional digits."""
    fraction = _FRACTION.search(text)
    digits = "" if fraction is None else fraction.group(1)
    return Timestamp.from_moment(datetime.fromisoformat(text), int((digits + "000000000")[:9]))


def now_id() -> str:
    """A session identifier: UTC, nine fractional digits, without the dot.

    The format is fixed, so every identifier stays a legal directory name.
    """
    return format_id(now(), keep_dot=False)


def now_turn_id() -> str:
    """A turn identifier: the same instant, but the fraction keeps its dot."""
    return format_id(now(), keep_dot=True)


def _nanoseconds(moment: datetime) -> int:
    """``moment``'s fractional seconds at nanosecond resolution."""
    return moment.nanoseconds if isinstance(moment, Timestamp) else moment.microsecond * _NANOS_PER_MICROSECOND


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
