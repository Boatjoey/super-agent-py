"""The command-line surface: a hand-written flag parser.

The parsing rules are the contract, so this parser reproduces them deliberately
rather than reaching for ``argparse``:

* ``-x`` and ``--x`` are the same flag, and ``--`` alone ends the flags;
* a bool flag never consumes the next token: ``-yolo=false`` sets it false while
  ``-yolo false`` leaves ``false`` as a positional argument;
* a value flag takes ``-cwd=/tmp`` and ``-cwd /tmp`` alike;
* there are no subcommands, no positional arguments, and no ``--version``;
* parsing stops at the first argument that is not a flag.

:func:`Parse` reports by raising :class:`FlagError` rather than exiting, so the
entry point owns the exit status: 2 for a flag error, 0 for ``-h``. Printing and
exiting are deliberately left to the caller.
"""

from __future__ import annotations

import dataclasses
import sys
from collections.abc import Sequence
from typing import Final

from super_agent.app.config import Flags

__all__ = ["FLAGS", "Flag", "FlagError", "parse", "usage"]

#: The two sets of boolean spellings accepted, and nothing else.
TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "t", "T", "TRUE", "true", "True"})
FALSE_VALUES: Final[frozenset[str]] = frozenset({"0", "f", "F", "FALSE", "false", "False"})


@dataclasses.dataclass(frozen=True, slots=True)
class Flag:
    """One flag declaration."""

    name: str
    is_bool: bool
    usage: str


#: Every flag this program defines. They are sorted by name when printed.
FLAGS: Final[tuple[Flag, ...]] = (
    Flag(name="yolo", is_bool=True, usage="Auto-approve tool execution"),
    Flag(name="no-tools", is_bool=True, usage="Disable tool calling"),
    Flag(name="approval-mode", is_bool=False, usage="Permission mode: ask, accept-edits, plan, bypass"),
    Flag(name="cwd", is_bool=False, usage="Project directory (defaults to nearest Git root)"),
)


class FlagError(Exception):
    """A parse outcome the entry point turns into an exit.

    ``status`` is 2 for a parse failure and 0 when ``-h`` asked for the usage.
    """

    __slots__ = ("message", "showUsage", "status")

    def __init__(self, message: str, status: int = 2, *, showUsage: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.showUsage = showUsage


def usage() -> str:
    """The flag set's name, then every flag by name.

    `` (default ...)`` would be appended only for a flag whose default is not the
    zero value of its type. Every flag here defaults to ``false`` or ``""``, so
    the text names no defaults at all — which is why nothing enables ``-yolo`` by
    being printed.
    """
    lines = [f"Usage of {sys.argv[0]}:"]
    for flag in sorted(FLAGS, key=lambda item: item.name):
        name = "" if flag.is_bool else " string"
        # Four spaces then a tab: the alignment used for a flag name longer than
        # one character, which every flag here is.
        lines.append(f"  -{flag.name}{name}\n    \t{flag.usage}")
    return "\n".join(lines) + "\n"


def parse(argv: Sequence[str]) -> Flags:
    """Parse ``argv``, or raise :class:`FlagError`."""
    byName = {flag.name: flag for flag in FLAGS}
    values: dict[str, str] = {flag.name: ("false" if flag.is_bool else "") for flag in FLAGS}
    index = 0
    while index < len(argv):
        argument = argv[index]
        if len(argument) < 2 or argument[0] != "-":
            break
        minuses = 1
        if argument[1] == "-":
            minuses = 2
            if len(argument) == 2:  # "--" terminates the flags
                break
        name = argument[minuses:]
        if name == "" or name[0] in "-=":
            raise FlagError(f"bad flag syntax: {argument}")
        index += 1
        value = ""
        hasValue = "=" in name[1:]
        if hasValue:
            name, value = name.split("=", 1)
        flag = byName.get(name)
        if flag is None:
            if name in ("help", "h"):  # the one special case the parser has
                raise FlagError("", status=0)
            raise FlagError(f"flag provided but not defined: -{name}")
        if flag.is_bool:
            if not hasValue:
                value = "true"
        else:
            if not hasValue and index < len(argv):
                value = argv[index]
                index += 1
                hasValue = True
            if not hasValue:
                raise FlagError(f"flag needs an argument: -{name}")
        values[name] = value
    return Flags(
        auto_approve_tools=_boolValue(values["yolo"], "yolo"),
        no_tools=_boolValue(values["no-tools"], "no-tools"),
        permission_mode=values["approval-mode"],
        cwd=values["cwd"],
    )


def _boolValue(value: str, name: str) -> bool:
    """The two accepted boolean spellings, or a parse failure."""
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise FlagError(f'invalid boolean value "{value}" for -{name}: parse error')
