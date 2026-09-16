"""``tests/MIGRATION_MAP.md`` is what makes "the port is complete" checkable.

The plan for this repository promises that every Go test function has a named
Python counterpart. That promise is only worth anything if a machine checks it, so
the map is a checked-in file and this module is the audit:

* every Go test named in the map must have a Python test that really exists;
* no Go test may appear twice;
* when the Go repository is checked out beside this one, the map's Go column must
  match the Go tests that are actually there — so a test added upstream shows up
  as a failure rather than as silent drift.

The Go repository is a sibling directory and is absent in CI, which is why the
last check reports a skip rather than passing quietly: a check that cannot run
must say so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = REPO_ROOT / "tests" / "MIGRATION_MAP.md"
GO_REPOSITORY = REPO_ROOT.parent / "super-agent-go"

_GO_TEST = re.compile(r"^func (Test[A-Za-z0-9_]+)\(", re.M)
_PYTHON_TEST = re.compile(r"^(?:    )?(?:async )?def (test_[a-z0-9_]+)\(", re.M)
_MAP_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|[^|]*\|$")

#: The map's header row and its separator, which are not data.
_HEADER_CELLS = {"go test", "go file", "python test", "note", "notes"}


@dataclass(frozen=True)
class MapRow:
    """One line of the map."""

    go_file: str
    go_test: str
    python_test: str

    @property
    def python_name(self) -> str:
        return self.python_test.rsplit("::", 1)[-1]


def go_tests_in_map() -> list[MapRow]:
    assert MAP_PATH.is_file(), f"missing {MAP_PATH}"
    rows: list[MapRow] = []
    for line in MAP_PATH.read_text(encoding="utf-8").splitlines():
        match = _MAP_ROW.match(line)
        if match is None:
            continue
        cells = [cell.strip().strip("`") for cell in match.groups()]
        if cells[0].lower() in _HEADER_CELLS or set(cells[0]) <= set("-: "):
            continue
        rows.append(MapRow(go_file=cells[0], go_test=cells[1], python_test=cells[2]))
    assert rows, f"{MAP_PATH} has no data rows; the table format changed"
    return rows


def python_tests_by_module() -> dict[str, set[str]]:
    """Every ``def test_...`` under ``tests/``, keyed by module path."""
    found: dict[str, set[str]] = {}
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        names = set(_PYTHON_TEST.findall(path.read_text(encoding="utf-8")))
        if names:
            found[path.relative_to(REPO_ROOT).as_posix()] = names
    return found


def go_tests_in_repository() -> set[str]:
    """Every Go test function in the sibling Go repository, and its file."""
    names: set[str] = set()
    for path in sorted(GO_REPOSITORY.glob("tests/*/*_test.go")):
        names.update(_GO_TEST.findall(path.read_text(encoding="utf-8")))
    return names


def test_map_lists_every_go_test_once() -> None:
    rows = go_tests_in_map()
    names = [row.go_test for row in rows]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f"these Go tests appear more than once in the map: {duplicates}"


def test_every_mapped_go_test_has_a_python_test() -> None:
    """The promise this repository makes: no Go test is left behind."""
    available = python_tests_by_module()
    missing: list[str] = []
    for row in go_tests_in_map():
        if row.python_test == "--":
            continue
        module, _, name = row.python_test.rpartition("::")
        if name not in available.get(module, set()):
            missing.append(f"{row.go_test} -> {row.python_test}")
    assert not missing, "mapped to a Python test that does not exist:\n  " + "\n  ".join(missing)


def test_map_matches_the_go_repository() -> None:
    """Drift detection against the real Go repository, when it is present."""
    if not GO_REPOSITORY.is_dir():
        pytest.skip(f"{GO_REPOSITORY} is not checked out; run this where both repositories are")

    upstream = go_tests_in_repository()
    assert upstream, f"found no Go tests under {GO_REPOSITORY}"

    documented = {row.go_test for row in go_tests_in_map() if row.python_test != "--"}
    documented |= {row.go_test for row in go_tests_in_map() if row.python_test == "--"}
    undocumented = sorted(upstream - documented)
    stale = sorted(documented - upstream)
    assert not undocumented, f"Go tests missing from the map: {undocumented}"
    assert not stale, f"map names Go tests that no longer exist: {stale}"


def test_every_test_module_is_accounted_for() -> None:
    """Every module holding tests is either a mapping target or declared extra.

    Checked at module granularity on purpose. A per-test check would force the map
    to list every parametrised variant and every deliberate extra by hand, which is
    how a map stops being read. Module granularity still catches the failure that
    matters: a whole new test module appearing without anyone saying what it is
    for.
    """
    targets: set[str] = {
        row.python_test.rpartition("::")[0] if "::" in row.python_test else row.python_test
        for row in go_tests_in_map()
        if row.python_test != "--"
    }
    targets |= declared_python_only_modules()

    unaccounted = [
        module
        for module in python_tests_by_module()
        if not module.endswith("tests/architecture/test_migration_map.py") and module not in targets
    ]
    assert not unaccounted, (
        "test modules that are neither a mapping target nor declared in the "
        "Python-only coverage table:\n  " + "\n  ".join(sorted(unaccounted))
    )


def declared_python_only_modules() -> set[str]:
    """The module names in the map's "Python-only coverage" table."""
    section = MAP_PATH.read_text(encoding="utf-8").split("## Python-only coverage", 1)
    assert len(section) == 2, f"{MAP_PATH} has no 'Python-only coverage' section"
    cells = re.findall(r"^\|\s*([^|]+?)\s*\|", section[1], re.M)
    return {cell.strip().strip("`") for cell in cells if cell.strip().strip("`").endswith(".py")}


def test_declared_python_only_modules_exist() -> None:
    """An extras table naming a module that is gone is stale documentation."""
    available = set(python_tests_by_module())
    missing = sorted(name for name in declared_python_only_modules() if name not in available)
    assert not missing, f"declared as Python-only but not present: {missing}"
