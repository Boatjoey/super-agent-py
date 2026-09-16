"""Layered project instructions.

Three layers, outermost first:

1. the user-level spec at ``~/.superagent/AGENTS.md``, always first;
2. every ``AGENTS.md`` on the path from the project root down to the working
   directory;
3. a ``CLAUDE.md`` in the same directory, but only when that directory's
   ``AGENTS.md`` is missing **or empty** — an empty file provides no guidance, so
   it must not silently suppress the fallback.

The walk stops at the nearest directory containing ``.git``. Directories above the
topmost one that holds any instruction file are trimmed, so a stray ``AGENTS.md``
in ``/`` cannot change what a project sees.

The driver ``LoadProjectInstructions`` lives here, and ``super_agent.app``
re-exports it under the same name so callers keep writing
``app.LoadProjectInstructions``.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Final

#: The largest instruction file that is read, in bytes.
MaxFileSize: Final[int] = 128 * 1024

#: The user-level configuration directory spelling: no hyphen, under home.
USER_CONFIG_DIRECTORY: Final[str] = ".superagent"

#: Instruction file names, in the order each directory is checked.
INSTRUCTION_FILES: Final[tuple[str, ...]] = ("AGENTS.md", "CLAUDE.md")


@dataclasses.dataclass(frozen=True, slots=True)
class Source:
    """One file that contributed to a bundle."""

    Path: str = ""
    Kind: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Bundle:
    """The concatenated instructions and where they came from."""

    Content: str = ""
    Sources: tuple[Source, ...] = ()


def Load(cwd: str) -> Bundle:
    """Load every instruction layer that applies to ``cwd``."""
    home = os.path.expanduser("~")
    user_path = os.path.join(home, USER_CONFIG_DIRECTORY, "AGENTS.md")

    parts: list[str] = []
    sources: list[Source] = []
    _append_source(parts, sources, user_path, "user-spec")

    for directory in ancestorDirs(cwd):
        agent_path = os.path.join(directory, "AGENTS.md")
        if _append_source(parts, sources, agent_path, "project"):
            continue
        _append_source(parts, sources, os.path.join(directory, "CLAUDE.md"), "claude-compat")

    return Bundle(Content="\n\n".join(parts), Sources=tuple(sources))


def LoadProjectInstructions(directory: str) -> str:
    """The concatenated instruction text for ``directory``."""
    return Load(directory).Content


def ancestorDirs(cwd: str) -> list[str]:
    """Directories from the project root down to ``cwd``, outermost first."""
    absolute = os.path.abspath(cwd)
    if not os.path.isdir(absolute):
        absolute = os.path.dirname(absolute)
    reversed_dirs = [absolute]
    while True:
        if _has_git_dir(absolute):
            break
        parent = os.path.dirname(absolute)
        if parent == absolute:
            break
        absolute = parent
        reversed_dirs.append(absolute)
    trimmed = _trim_above_topmost_instruction_dir(reversed_dirs)
    return list(reversed(trimmed))


def _append_source(parts: list[str], sources: list[Source], path: str, kind: str) -> bool:
    """Append ``path``'s contents, reporting whether it contributed anything."""
    if not os.path.exists(path) or os.path.isdir(path):
        return False
    size = os.path.getsize(path)
    if size > MaxFileSize:
        raise ValueError(f"instruction file {path} is too large: {size} bytes exceeds {MaxFileSize} bytes")
    text = _read_text(path).strip()
    if text == "":
        # An empty AGENTS.md provides no guidance, so it must not suppress the
        # CLAUDE.md fallback in the same directory.
        return False
    parts.append(text)
    sources.append(Source(Path=path, Kind=kind))
    return True


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _has_git_dir(directory: str) -> bool:
    """Whether ``directory`` holds a ``.git`` directory or file.

    A file counts: a worktree or submodule checkout writes ``.git`` as a file.
    """
    git_path = os.path.join(directory, ".git")
    return os.path.isdir(git_path) or os.path.isfile(git_path)


def _trim_above_topmost_instruction_dir(reversed_dirs: list[str]) -> list[str]:
    """Drop the directories above the topmost one holding an instruction file."""
    for index in range(len(reversed_dirs) - 1, -1, -1):
        if _has_instruction_file(reversed_dirs[index]):
            return reversed_dirs[: index + 1]
    if not reversed_dirs:
        return reversed_dirs
    return reversed_dirs[:1]


def _has_instruction_file(directory: str) -> bool:
    """Whether ``directory`` holds one of the instruction files.

    Stat failures other than "missing" are raised rather than read as "no file":
    instructions must never vanish silently because a parent directory became
    unreadable.
    """
    for name in INSTRUCTION_FILES:
        path = os.path.join(directory, name)
        try:
            is_directory = os.path.isdir(path)
            os.stat(path)
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError(f"cannot check instruction file in {directory}: {error}") from error
        if not is_directory:
            return True
    return False


__all__ = [
    "INSTRUCTION_FILES",
    "USER_CONFIG_DIRECTORY",
    "Bundle",
    "Load",
    "LoadProjectInstructions",
    "MaxFileSize",
    "Source",
    "ancestorDirs",
]
