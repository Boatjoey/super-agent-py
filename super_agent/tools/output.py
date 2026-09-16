"""The capped sink a command's merged output is written into.

Truncation happens while reading, not after: buffering the whole stream and
trimming afterwards means a command that emits gigabytes is still read into memory
in full before the limit is applied.
"""

from __future__ import annotations


class capped_buffer:
    """Collects at most ``limit`` bytes of command output."""

    __slots__ = ("_buf", "_limit", "_truncated")

    def __init__(self, limit: int) -> None:
        self._buf = bytearray()
        self._limit = limit
        self._truncated = False

    def write(self, chunk: bytes) -> int:
        room = self._limit - len(self._buf)
        if room > 0:
            if len(chunk) <= room:
                self._buf.extend(chunk)
                return len(chunk)
            self._buf.extend(chunk[:room])
        self._truncated = True
        # Report a full write so the child keeps running. Returning a short
        # count would look like a write error and could surface as a broken
        # pipe.
        return len(chunk)

    def String(self) -> str:
        """The collected output, with the truncation marker when it was cut."""
        out = bytes(self._buf).decode("utf-8", "replace")
        if self._truncated:
            out += "\n... truncated"
        return out
