"""Editing the composer draft in the user's own editor.

``docs/tui.md`` gives ``Ctrl+G`` one job: hand the draft to ``$VISUAL`` or
``$EDITOR``, and put the terminal back afterwards. That is all this module does,
and it is deliberately a plain function rather than a feature: it owns no state,
draws nothing, and its whole outcome is the text the user saved.

The two halves are separate on purpose. :func:`resolve_editor` is pure — a
mapping in, an argv out — so what the environment means is testable without a
terminal, and :func:`edit_draft` is the imperative half that writes a temporary
file, suspends the interface, and runs the editor while it owns the terminal.

Suspend is the crux. ``App.suspend`` is a plain context manager (Textual 8 has
no async form), and the ``await`` inside it is what leaves the terminal to the
editor for as long as it runs; the driver resumes application mode when the
block exits, whether the editor succeeded or not. The temporary file is removed
in a ``finally``, so an editor that crashes, a user who cancels the turn mid-edit
(``asyncio.CancelledError`` is a ``BaseException`` and passes through ``except
Exception``), and a file the editor deletes rather than saves all leave no litter.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import tempfile
from collections.abc import Mapping
from pathlib import Path

from textual.app import App

__all__ = ["EDITOR_ENV", "edit_draft", "resolve_editor"]

#: Checked in order; the first one that names a program wins. ``VISUAL`` comes
#: first because that is what the pair means: the visual editor is the one meant
#: for a full-screen terminal, and this is a full-screen terminal.
EDITOR_ENV: tuple[str, ...] = ("VISUAL", "EDITOR")


def resolve_editor(environ: Mapping[str, str]) -> tuple[str, ...] | None:
    """The editor command line, split into argv, or ``None`` when unset/blank.

    The value is split with :func:`shlex.split`, so ``EDITOR="code --wait"``
    names a program and its arguments. A blank or whitespace-only value counts as
    unset, and ``None`` is returned rather than an exception, because a missing
    editor is an ordinary thing for an environment to have.

    A value that cannot be split at all — unbalanced quotes, a trailing
    backslash — does not name a program either, so resolution moves on to the
    next variable and eventually to ``None``. Raising over a malformed
    environment would put the failure in the middle of the interface instead of
    in the hands of the user who set the variable.
    """
    for name in EDITOR_ENV:
        value = environ.get(name, "")
        if not value.strip():
            continue
        try:
            argv = shlex.split(value)
        except ValueError:
            continue
        if argv:
            return tuple(argv)
    return None


async def edit_draft(app: App[None], draft: str, *, suffix: str = ".md") -> str | None:
    """Suspend the TUI, let the user edit the draft, and return the saved text.

    Returns ``None`` when there is no editor configured, when the editor cannot
    be started, when it exits non-zero, when it leaves no file behind, or when
    the file comes back unchanged — each of which means the same thing to the
    caller: there is nothing to replace the draft with. A misconfigured
    environment must not take the interface down with it, so a program that does
    not exist is answered the same way as one that failed.

    The suffix is what an editor keys its syntax highlighting off, which is why
    it is a parameter rather than a constant.
    """
    argv = resolve_editor(os.environ)
    if argv is None:
        return None

    # The name is kept from the start and the file is removed in the ``finally``
    # below, so an editor that crashes cannot leave a draft of someone's prompt
    # on disk. ``mkstemp`` also creates it readable only by its owner, which a
    # draft deserves: it is whatever the user was about to send.
    descriptor, name = tempfile.mkstemp(suffix=suffix, prefix="super-agent-")
    target = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(draft)
        try:
            with app.suspend():
                process = await asyncio.create_subprocess_exec(*argv, str(target))
                exit_code = await process.wait()
        except OSError:
            # Configured, but not startable: no such program, or one without the
            # execute bit. The draft stands, and the terminal is already back.
            return None
        if exit_code != 0:
            return None
        try:
            edited = target.read_text(encoding="utf-8")
        except OSError:
            return None
        return None if edited == draft else edited
    finally:
        with contextlib.suppress(OSError):
            target.unlink()
