"""Permission decision value types.

Command classification routes approvals; it is not a security boundary. The
policy in ``runtime/execution`` is what decides, and these are only the values it
reasons about and hands to the TUI.
"""

from __future__ import annotations

import dataclasses
from typing import Final


class CommandClass(str):
    """How a shell command is classified. Mirrors Go's string-backed type."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"CommandClass({str.__repr__(self)})"


CommandClassReadOnly: Final[CommandClass] = CommandClass("read-only")
CommandClassWrite: Final[CommandClass] = CommandClass("write")
CommandClassNetwork: Final[CommandClass] = CommandClass("network")
CommandClassDestructive: Final[CommandClass] = CommandClass("destructive")
CommandClassUnknown: Final[CommandClass] = CommandClass("unknown")

#: Go's zero value for the type. A request nobody classified is not the same as
#: one classified ``unknown``: ``unknown`` means the command was examined and
#: found opaque, while the zero value means nothing looked at it. The TUI keeps
#: the distinction — ``tui/approval`` only prints the class line when it is set.
ZeroCommandClass: Final[CommandClass] = CommandClass("")


@dataclasses.dataclass(frozen=True, slots=True)
class Request:
    """Why a tool call needs a decision.

    ``runtime/permission`` declares no JSON tags, so serialising this with
    :mod:`super_agent.jsonutil` falls back to the field names — which is exactly
    what Go's encoder does with a tagless struct. Nothing persists it either way.
    """

    ToolName: str = ""
    Command: str = ""
    CommandClass: CommandClass = ZeroCommandClass
    CWD: str = ""
    TouchedPaths: tuple[str, ...] = ()
    EnvVars: tuple[str, ...] = ()
    Reason: str = ""
