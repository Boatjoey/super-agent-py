"""The message and command vocabulary the interactive CLI loop runs on.

The loop itself belongs to the application shell: it owns the queue, the task
that drains a command, and the listener a new turn replaces. What lives here is
only the vocabulary those pieces share — the messages a key press, a resize, a
quit, or a status clear becomes, and the ``Command`` a feature hands back for
the shell to run.

Three things are worth naming:

* :class:`Listener` marks a command that waits on a channel until its turn ends.
  The shell keeps the task so a new turn can cancel the previous listener
  instead of leaking it.
* A command is an awaitable producing at most one message, so a feature never
  reaches the queue itself.
* ``QUIT`` and ``CLEAR_SCREEN`` are values rather than async functions, so a
  caller passes them, never calls them.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from typing import Final

__all__ = [
    "CLEAR_SCREEN",
    "QUIT",
    "ClearScreenMsg",
    "Command",
    "KeyMsg",
    "Listener",
    "QuitMsg",
    "WindowSizeMsg",
    "batch",
]

type Command[M] = Callable[[], Awaitable[M | None]]
"""A unit of work the shell runs: an awaitable producing at most one message."""


@dataclasses.dataclass(frozen=True, slots=True)
class WindowSizeMsg:
    """The terminal was resized, or its size is known for the first time."""

    width: int = 80
    height: int = 24


@dataclasses.dataclass(frozen=True, slots=True)
class KeyMsg:
    """One key press, named with the CLI's canonical key vocabulary.

    ``key`` is the canonical name: a single character for a printable key,
    otherwise ``enter``, ``tab``, ``esc``, ``up``, ``down``, ``ctrl+c``,
    ``alt+o``, ``shift+enter``, and so on. The features switch on these names,
    so translating a terminal's own key report into one of them is the shell's
    job.
    """

    key: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class QuitMsg:
    """Stop the program."""


@dataclasses.dataclass(frozen=True, slots=True)
class ClearScreenMsg:
    """Drop the transient status and status-line state."""


async def _quit() -> QuitMsg:
    return QuitMsg()


async def _clear_screen() -> ClearScreenMsg:
    return ClearScreenMsg()


#: The command that stops the program. It is a value rather than an async
#: function, so a caller passes it, never calls it.
QUIT: Final[Command[QuitMsg]] = _quit

#: The command that clears the transient status.
CLEAR_SCREEN: Final[Command[ClearScreenMsg]] = _clear_screen


@dataclasses.dataclass(frozen=True, slots=True)
class Listener[M]:
    """A command that waits on a channel until the turn it belongs to ends.

    A new turn replaces the previous listener, so the shell cancels the task the
    old one runs in rather than leaving it blocked until its channel closes.
    """

    command: Command[M]

    async def __call__(self) -> M | None:
        return await self.command()


def batch[M](*commands: Command[M] | None) -> tuple[Command[M], ...]:
    """Collect commands, dropping the ones that do nothing."""
    return tuple(command for command in commands if command is not None)
