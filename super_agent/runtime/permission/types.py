"""Permission decision value types.

Command classification routes approvals; it is not a security boundary. The
policy in ``runtime/execution`` is what decides, and these are only the values it
reasons about and hands to the TUI.
"""

from __future__ import annotations

import dataclasses
from typing import Final


class CommandClass(str):
    """How a shell command is classified, as a string-backed type."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"CommandClass({str.__repr__(self)})"


COMMAND_CLASS_READ_ONLY: Final[CommandClass] = CommandClass("read-only")
COMMAND_CLASS_WRITE: Final[CommandClass] = CommandClass("write")
COMMAND_CLASS_NETWORK: Final[CommandClass] = CommandClass("network")
COMMAND_CLASS_DESTRUCTIVE: Final[CommandClass] = CommandClass("destructive")
COMMAND_CLASS_UNKNOWN: Final[CommandClass] = CommandClass("unknown")

#: The zero value for the type. A request nobody classified is not the same as
#: one classified ``unknown``: ``unknown`` means the command was examined and
#: found opaque, while the zero value means nothing looked at it. The TUI keeps
#: the distinction — ``tui/approval`` only prints the class line when it is set.
ZERO_COMMAND_CLASS: Final[CommandClass] = CommandClass("")


@dataclasses.dataclass(frozen=True, slots=True)
class Request:
    """Why a tool call needs a decision.

    ``runtime/permission`` declares no JSON tags, so serialising this with
    :mod:`super_agent.jsonutil` falls back to the field names, which is the
    documented fallback for a type with no tags. Nothing persists it either way.
    """

    tool_name: str = ""
    command: str = ""
    command_class: CommandClass = ZERO_COMMAND_CLASS
    cwd: str = ""
    touched_paths: tuple[str, ...] = ()
    env_vars: tuple[str, ...] = ()
    reason: str = ""
