"""The external editor: the environment knob, and the round trip to a subprocess.

Two seams make this testable without a terminal, and both keep the behaviour
real rather than faked.

* The editor program is a real ``python -c`` process whose body the test writes.
  It receives the draft's path as ``sys.argv[1]``, exactly as a terminal editor
  does, so the round trip — draft written, editor rewrites the file, edited text
  read back — runs through a genuine subprocess and a genuine file.
* ``App.suspend`` is replaced, because handing the terminal over needs a driver
  and a test has no terminal. The stand-in appends to a log that the editor
  process appends to as well, which is what proves the subprocess ran *inside*
  the suspend block rather than merely near it.

What neither seam covers is Textual's own half: that suspending stops the driver
reading input and that leaving the block resumes it. That is Textual's behaviour,
not this module's, and no test here can observe a terminal that is not there.
"""

from __future__ import annotations

import asyncio
import contextlib
import shlex
import sys
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest
from textual.app import App as TextualApp

from super_agent.tui.editor import EDITOR_ENV, edit_draft, resolve_editor


class BareApp(TextualApp[None]):
    """Nothing but an application to suspend: ``edit_draft`` reaches for no more."""


def append(path: Path, line: str) -> None:
    """Append one line to the test's shared log.

    Two processes write to it — the suspend stand-in and the editor — so it is
    opened for append rather than held open, and its order is the evidence.
    """
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def scripted_editor(*lines: str, log: Path | None = None) -> str:
    """A value for ``$EDITOR`` that runs ``lines`` with the draft as ``argv[1]``.

    The script is run by this interpreter, so no terminal editor is needed and no
    shell is involved: the returned string is an argv in the form a user would
    write it, which is exactly what :func:`resolve_editor` has to split.
    """
    body = ["import sys", "from pathlib import Path", "draft = Path(sys.argv[1])"]
    if log is not None:
        body.append(f"open({str(log)!r}, 'a').write('edit\\n')")
    body.extend(lines)
    return " ".join(shlex.quote(part) for part in (sys.executable, "-c", "\n".join(body)))


def use_editor(monkeypatch: pytest.MonkeyPatch, command: str, *, variable: str = "VISUAL") -> None:
    """Set one editor variable and make sure the other one is not in the way."""
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setenv(variable, command)


def use_no_editor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave the process with nothing configured, whatever the developer has."""
    for name in EDITOR_ENV:
        monkeypatch.delenv(name, raising=False)


def suspend_seam(monkeypatch: pytest.MonkeyPatch, log: Path) -> None:
    """Replace the terminal handover with a recording stand-in."""

    @contextlib.contextmanager
    def suspend(self: TextualApp[None]) -> Generator[None]:
        append(log, "suspend")
        try:
            yield
        finally:
            append(log, "resume")

    monkeypatch.setattr(TextualApp, "suspend", suspend)


def draft_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``tempfile`` at a directory this test can inspect afterwards."""
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(drafts))
    return drafts


def events(log: Path) -> list[str]:
    """The log's lines, in the order the two processes wrote them."""
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def leftovers(drafts: Path) -> list[str]:
    """Whatever the call left behind in the temporary directory."""
    return sorted(path.name for path in drafts.iterdir())


# ---------------------------------------------------------------------------
# The environment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, None),
        ({"EDITOR": ""}, None),
        ({"EDITOR": "   "}, None),
        ({"VISUAL": "", "EDITOR": "\t "}, None),
        ({"EDITOR": "nano"}, ("nano",)),
        ({"VISUAL": "vim", "EDITOR": "nano"}, ("vim",)),
        ({"VISUAL": "  ", "EDITOR": "nano"}, ("nano",)),
        ({"VISUAL": "", "EDITOR": "  nano  "}, ("nano",)),
        ({"EDITOR": "code --wait"}, ("code", "--wait")),
        ({"EDITOR": 'nvim "+set ft=markdown"'}, ("nvim", "+set ft=markdown")),
        ({"EDITOR": "  code   --wait  "}, ("code", "--wait")),
        ({"EDITOR": 'vim "unbalanced'}, None),
        ({"EDITOR": "vim trailing\\"}, None),
        ({"VISUAL": 'vim "unbalanced', "EDITOR": "nano"}, ("nano",)),
    ],
)
def test_resolve_editor_reads_the_environment(environ: dict[str, str], expected: tuple[str, ...] | None) -> None:
    """A malformed value is not a program either, so resolution moves on rather than raising."""
    assert resolve_editor(environ) == expected


def test_the_editor_variables_are_checked_in_the_documented_order() -> None:
    """``VISUAL`` is the full-screen editor, which is the one this terminal wants."""
    assert EDITOR_ENV == ("VISUAL", "EDITOR")


def test_resolve_editor_never_raises_on_a_malformed_value() -> None:
    """The command must not fail because an environment variable is misquoted."""
    for value in ('"', "'", "\\", 'vim "', "a 'b", "t-\\", 'a "b:c'):
        assert resolve_editor({"VISUAL": value}) is None, value
        assert resolve_editor({"VISUAL": value, "EDITOR": "nano"}) == ("nano",), value


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_draft_returns_what_the_editor_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "log"
    use_editor(
        monkeypatch,
        scripted_editor("draft.write_text(draft.read_text() + 'edited\\n')", log=log),
    )
    suspend_seam(monkeypatch, log)

    result = await edit_draft(BareApp(), "the original draft\n")

    assert result == "the original draft\nedited\n"


@pytest.mark.asyncio
async def test_edit_draft_writes_the_draft_where_the_editor_can_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "log"
    use_editor(
        monkeypatch,
        scripted_editor(f"open({str(log)!r}, 'a').write(repr(draft.read_text()) + '\\n')"),
    )
    suspend_seam(monkeypatch, log)

    await edit_draft(BareApp(), "  draft \nwith lines  ")

    assert repr("  draft \nwith lines  ") in log.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_edit_draft_runs_the_editor_inside_the_suspend_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The terminal is the editor's while it runs, so the block has to span it."""
    log = tmp_path / "log"
    use_editor(monkeypatch, scripted_editor("draft.write_text('edited')", log=log))
    suspend_seam(monkeypatch, log)

    await edit_draft(BareApp(), "draft")

    assert events(log) == ["suspend", "edit", "resume"]


@pytest.mark.asyncio
async def test_edit_draft_gives_the_file_the_suffix_it_was_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suffix is what an editor keys its syntax highlighting off."""
    log = tmp_path / "log"
    use_editor(
        monkeypatch,
        scripted_editor(f"open({str(log)!r}, 'a').write(draft.suffix + '\\n')"),
    )
    suspend_seam(monkeypatch, log)

    await edit_draft(BareApp(), "draft")
    await edit_draft(BareApp(), "draft", suffix=".py")

    assert events(log) == ["suspend", ".md", "resume", "suspend", ".py", "resume"], (
        "markdown by default, as the composer's drafts are, and the caller's to choose"
    )


@pytest.mark.asyncio
async def test_edit_draft_falls_back_to_the_editor_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "log"
    use_editor(monkeypatch, scripted_editor("draft.write_text('from EDITOR')"), variable="EDITOR")
    suspend_seam(monkeypatch, log)

    assert await edit_draft(BareApp(), "draft") == "from EDITOR"


@pytest.mark.asyncio
async def test_edit_draft_appends_the_draft_to_the_editor_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``EDITOR="code --wait"`` names a program *and* its arguments; the file follows them.

    That the interpreter here understood its own multi-word ``-c`` body at all is
    the other half of the claim: the command was split into arguments by the
    environment's quoting rules, and the draft arrived as one more of them.
    """
    log = tmp_path / "log"
    script = scripted_editor(f"open({str(log)!r}, 'a').write(repr(sys.argv[1:]) + '\\n')")
    use_editor(monkeypatch, script)

    suspend_seam(monkeypatch, log)
    await edit_draft(BareApp(), "draft")

    recorded = events(log)[1]
    assert recorded.startswith("['/") and recorded.endswith(".md']"), recorded
    assert " " not in recorded, "the draft's path is one argument, not several"


# ---------------------------------------------------------------------------
# What counts as no edit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_missing_editor_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "log"
    drafts = draft_directory(tmp_path, monkeypatch)
    use_no_editor(monkeypatch)
    suspend_seam(monkeypatch, log)

    assert await edit_draft(BareApp(), "draft") is None
    assert events(log) == [], "with nowhere to edit there is nothing to suspend for"
    assert leftovers(drafts) == [], "and nothing is written"


@pytest.mark.asyncio
async def test_an_unchanged_draft_reports_no_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An editor that saves nothing means the draft stands, not that it is empty."""
    log = tmp_path / "log"
    use_editor(monkeypatch, scripted_editor("pass"))
    suspend_seam(monkeypatch, log)

    assert await edit_draft(BareApp(), "the draft") is None


@pytest.mark.asyncio
async def test_a_draft_rewritten_unchanged_reports_no_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The file coming back identical is the same answer as not touching it."""
    log = tmp_path / "log"
    use_editor(monkeypatch, scripted_editor("draft.write_text(draft.read_text())"))
    suspend_seam(monkeypatch, log)

    assert await edit_draft(BareApp(), "the draft") is None


@pytest.mark.asyncio
async def test_a_failed_editor_reports_no_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-zero exit is the user aborting the editor, so the draft is kept."""
    log = tmp_path / "log"
    use_editor(monkeypatch, scripted_editor("draft.write_text('half a thought')", "sys.exit(3)"))
    suspend_seam(monkeypatch, log)

    assert await edit_draft(BareApp(), "the draft") is None


@pytest.mark.asyncio
async def test_an_editor_that_cannot_be_started_reports_no_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A misspelt ``$EDITOR`` keeps the draft rather than bringing the interface down."""
    log = tmp_path / "log"
    drafts = draft_directory(tmp_path, monkeypatch)
    use_editor(monkeypatch, "super-agent-no-such-editor --wait")
    suspend_seam(monkeypatch, log)

    assert await edit_draft(BareApp(), "the draft") is None
    assert leftovers(drafts) == [], "and the file it would have written is gone"


@pytest.mark.asyncio
async def test_an_editor_that_removes_the_file_reports_no_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """There is no text to read back, and that must not be an exception either."""
    log = tmp_path / "log"
    use_editor(monkeypatch, scripted_editor("draft.unlink()"))
    suspend_seam(monkeypatch, log)

    assert await edit_draft(BareApp(), "the draft") is None


# ---------------------------------------------------------------------------
# The temporary file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "script",
    [
        "pass",
        "draft.write_text(draft.read_text())",
        "draft.write_text('edited')",
        "draft.unlink()",
        "sys.exit(3)",
    ],
)
async def test_the_draft_file_is_removed_however_the_editor_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str
) -> None:
    """A save, an abort, a deletion, or a crash: none of them may leave litter."""
    log = tmp_path / "log"
    drafts = draft_directory(tmp_path, monkeypatch)
    use_editor(monkeypatch, scripted_editor(script))
    suspend_seam(monkeypatch, log)

    await edit_draft(BareApp(), "the draft")

    assert leftovers(drafts) == [], f"{script} left a file behind"


@pytest.mark.asyncio
async def test_a_cancelled_edit_removes_the_draft_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``CancelledError`` is a ``BaseException``: it must pass through and still clean up."""
    log = tmp_path / "log"
    drafts = draft_directory(tmp_path, monkeypatch)
    use_editor(monkeypatch, scripted_editor("draft.write_text('edited')"))

    @contextlib.contextmanager
    def suspend(self: TextualApp[None]) -> Generator[None]:
        append(log, "suspend")
        yield
        raise asyncio.CancelledError

    monkeypatch.setattr(TextualApp, "suspend", suspend)

    with pytest.raises(asyncio.CancelledError):
        await edit_draft(BareApp(), "the draft")

    assert events(log) == ["suspend"], "the editor really ran before the turn was cancelled"
    assert leftovers(drafts) == [], "cancelling is not a reason to leave the draft on disk"


@pytest.mark.asyncio
async def test_the_editor_command_is_not_interpreted_by_a_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command line is argv: a metacharacter in it is an argument, not a second command."""
    log = tmp_path / "log"
    marker = tmp_path / "marker"
    script = shlex.quote(sys.executable) + " -c " + shlex.quote("import sys\nsys.exit(1)")
    use_editor(monkeypatch, f"{script} ; touch {marker}")
    suspend_seam(monkeypatch, log)

    assert await edit_draft(BareApp(), "the draft") is None
    assert not marker.exists(), "the editor is exec'd, never run through a shell"
