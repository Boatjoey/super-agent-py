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

__all__ = ["FLAGS", "Flag", "FlagError", "Parse", "Usage"]

#: The two sets of boolean spellings accepted, and nothing else.
TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "t", "T", "TRUE", "true", "True"})
FALSE_VALUES: Final[frozenset[str]] = frozenset({"0", "f", "F", "FALSE", "false", "False"})


@dataclasses.dataclass(frozen=True, slots=True)
class Flag:
    """One flag declaration."""

    Name: str
    IsBool: bool
    Usage: str


#: Every flag this program defines. They are sorted by name when printed.
FLAGS: Final[tuple[Flag, ...]] = (
    Flag(Name="yolo", IsBool=True, Usage="Auto-approve tool execution"),
    Flag(Name="no-tools", IsBool=True, Usage="Disable tool calling"),
    Flag(Name="approval-mode", IsBool=False, Usage="Permission mode: ask, accept-edits, plan, bypass"),
    Flag(Name="cwd", IsBool=False, Usage="Project directory (defaults to nearest Git root)"),
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


def Usage() -> str:
    """The flag set's name, then every flag by name.

    `` (default ...)`` would be appended only for a flag whose default is not the
    zero value of its type. Every flag here defaults to ``false`` or ``""``, so
    the text names no defaults at all — which is why nothing enables ``-yolo`` by
    being printed.
    """
    lines = [f"Usage of {sys.argv[0]}:"]
    for flag in sorted(FLAGS, key=lambda item: item.Name):
        name = "" if flag.IsBool else " string"
        # Four spaces then a tab: the alignment used for a flag name longer than
        # one character, which every flag here is.
        lines.append(f"  -{flag.Name}{name}\n    \t{flag.Usage}")
    return "\n".join(lines) + "\n"


def Parse(argv: Sequence[str]) -> Flags:
    """Parse ``argv``, or raise :class:`FlagError`."""
    byName = {flag.Name: flag for flag in FLAGS}
    values: dict[str, str] = {flag.Name: ("false" if flag.IsBool else "") for flag in FLAGS}
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
        if flag.IsBool:
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
        AutoApproveTools=_boolValue(values["yolo"], "yolo"),
        NoTools=_boolValue(values["no-tools"], "no-tools"),
        PermissionMode=values["approval-mode"],
        CWD=values["cwd"],
    )


def _boolValue(value: str, name: str) -> bool:
    """The two accepted boolean spellings, or a parse failure."""
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    raise FlagError(f'invalid boolean value "{value}" for -{name}: parse error')
