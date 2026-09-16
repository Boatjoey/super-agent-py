#!/usr/bin/env python3
"""Regenerate ``tests/MIGRATION_MAP.md`` from the Go repository.

Run it whenever Go tests are added, renamed, or removed:

    uv run python scripts/migration-map.py

The map is the checked-in answer to "does every Go test have a Python
counterpart?", and ``tests/architecture/test_migration_map.py`` audits it. The
generator's only job is to keep the mechanical part — the Go column and the
derived Python name — honest; where a Python test was named differently, the
correction table below records it, so a rename is a deliberate edit rather than a
silent guess.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GO_REPOSITORY = REPO_ROOT.parent / "super-agent-go"
MAP_PATH = REPO_ROOT / "tests" / "MIGRATION_MAP.md"

_GO_TEST = re.compile(r"^func (Test[A-Za-z0-9_]+)\(", re.M)
_PYTHON_TEST = re.compile(r"^(?:    )?(?:async )?def (test_[a-z0-9_]+)\(", re.M)

#: Fragments the mechanical conversion produces that no reviewer would write.
SNAKE_FRAGMENT_FIXES: tuple[tuple[str, str], ...] = (
    ("open_ai", "openai"),
    ("i_ds", "ids"),
    ("scheme_less", "schemeless"),
    ("agentsmd", "agents_md"),
)

#: Where a reviewer chose a different name on purpose. Keyed by the Go test.
NAME_CORRECTIONS: dict[str, str] = {
    # The Go test asserts the absence of an Authorization header. The Python SDK
    # refuses to build a client with no credentials at all, so the port asserts
    # the property the test names: an empty configured key falls through to the
    # environment rather than shadowing it.
    "TestOpenAIModelSkipsAuthHeaderWithoutAPIKey": "test_openai_model_uses_environment_api_key_when_config_key_is_empty",
    # Split into two Python tests: one aggregates specs, one dispatches by name.
    "TestToolRegistryAggregatesSpecsAndDispatchesByName": "test_registry_aggregates_specs_in_order",
    "TestBashToolsExposeRiskyBashTool": "test_bash_tool_is_risky",
    "TestBashToolsExposeOnlyBashTool": "test_registry_holding_only_the_bash_tool_exposes_only_bash",
    "TestListAndSearchSkipOutsideSymlinks": "test_list_and_search_skip_symlinks_that_point_outside",
    "TestReadFileSupportsLineRange": "test_read_file_supports_a_line_range",
    "TestConcurrentRunTurnFailsWithoutBlockingEvents": "test_session_run_turn_refuses_a_second_turn",
}

#: Go tests whose Python counterpart lives in a differently named module, because
#: Go's file is large and the subject deserved its own module.
MODULE_CORRECTIONS: dict[str, str] = {
    # Both cases moved to the file that owns the engine and the session.
    "TestEngineRejectsInvalidPermissionMode": "tests/runtime/test_engine.py",
    "TestEngineDoesNotCommitInvalidCustomRuntimeDataChangeResult": "tests/runtime/test_engine.py",
    "TestSnapshotIncludesPendingToolBatchProgress": "tests/runtime/test_tool_batch.py",
    # The five TestSession* cases: Go keeps them in engine_test.go because the
    # session is what publishes notifications while the engine loop runs. They
    # get their own module here so the notification contract is one subject.
    "TestSessionRunEmitsStateAndFinalMessage": "tests/runtime/test_session_notifications.py",
    "TestSessionRunEmitsEachAppendedMessageOnce": "tests/runtime/test_session_notifications.py",
    "TestSessionEmitsToolApprovalClearedAfterApproval": "tests/runtime/test_session_notifications.py",
    "TestSessionStreamEventCarriesAccumulatedStreamingMessage": "tests/runtime/test_session_notifications.py",
    "TestSessionCloseClosesOwnedAdaptersOnce": "tests/runtime/test_session_notifications.py",
    "TestResolverErrorLeavesNoToolMessageWhenNothingWasAsked": "tests/runtime/test_session_notifications.py",
}

#: Go tests that have no Python test function because Python does not need one.
#: Each maps to ``--`` and must explain itself.
NON_PORTED: dict[str, str] = {
    "TestMCPHelperProcess": (
        "A helper entry point, not a test: Go re-executes its own binary to fake an MCP server. "
        "Python spawns `tests/helpers/mcp_echo_server.py` instead."
    ),
}

#: Modules whose tests exist with no Go counterpart, or that hold Python-only
#: additions beside their mapped ones. Every module here is a deliberate choice.
PYTHON_ONLY: dict[str, str] = {
    "tests/tools/test_workspace.py": (
        "Go leaves `tools/workspace.go` untested; these four cases pin the path-resolution helpers."
    ),
    "tests/runtime/test_command_analyzer.py": (
        "The Go policy tests exercise the analyzer indirectly. This module pins the classification "
        "table directly, because a regression there changes which approval prompt a user sees."
    ),
    "tests/app/test_config.py": (
        "Also holds the placeholder-credential regression test: Go resolves a provider credential and "
        "then builds the model from the unresolved config, so `sk-...` can be sent as a real bearer "
        "token. The port fixes that and this test keeps it fixed."
    ),
    "tests/architecture/test_dependencies.py": (
        "Extends the Go rules: R7 (runtime/machine performs no I/O) and R8 (a TUI feature does not "
        "import the root tui package) are Python additions."
    ),
    "tests/runtime/test_concurrency_stress.py": (
        "The stand-in for `go test -race`: every port call yields repeatedly while unrelated "
        "coroutines read snapshots and cancel, so the engine's atomicity claims are exercised "
        "against many interleavings rather than one."
    ),
    "tests/architecture/test_spec.py": (
        "Adds a test pinning the state and event counts, so a sixth state or sixteenth event cannot "
        "shrink the 6x15 sweep without a failure."
    ),
}


def snake_case(go_name: str) -> str:
    """``TestFooBarBaz`` -> ``test_foo_bar_baz``, keeping acronym runs intact."""
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", go_name[4:])
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    converted = "test_" + name.lower()
    for fragment, replacement in SNAKE_FRAGMENT_FIXES:
        converted = converted.replace(fragment, replacement)
    return converted


def collect_go_tests(go_repository: Path) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(go_repository.glob("tests/*/*_test.go")):
        names = _GO_TEST.findall(path.read_text(encoding="utf-8"))
        if names:
            found[path.relative_to(go_repository).as_posix()] = names
    return found


def collect_python_tests() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted((REPO_ROOT / "tests").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        names = set(_PYTHON_TEST.findall(path.read_text(encoding="utf-8")))
        if names:
            found[path.relative_to(REPO_ROOT).as_posix()] = names
    return found


def best_match(want: str, candidates: set[str]) -> str | None:
    matches = difflib.get_close_matches(want, sorted(candidates), n=1, cutoff=0.75)
    return matches[0] if matches else None


def build(go_repository: Path) -> tuple[list[tuple[str, str, str, str]], list[str], list[str]]:
    python = collect_python_tests()
    rows: list[tuple[str, str, str, str]] = []
    problems: list[str] = []
    notices: list[str] = []

    for go_file, names in collect_go_tests(go_repository).items():
        # tests/runtime/engine_test.go -> tests/runtime/test_engine.py
        go_path = Path(go_file)
        default_module = (go_path.parent / f"test_{go_path.stem.removesuffix('_test')}.py").as_posix()
        for go_name in names:
            if go_name in NON_PORTED:
                rows.append((go_file, go_name, "--", "helper"))
                continue
            module = MODULE_CORRECTIONS.get(go_name, default_module)
            available = python.get(module, set())
            want = NAME_CORRECTIONS.get(go_name, snake_case(go_name))
            if want in available:
                rows.append((go_file, go_name, f"{module}::{want}", ""))
                continue
            guess = best_match(want, available)
            if guess is not None:
                rows.append((go_file, go_name, f"{module}::{guess}", "renamed"))
                notices.append(f"{go_file}: {go_name} -> {module}::{guess} (mechanical name was {want})")
                continue
            rows.append((go_file, go_name, f"{module}::{want}", "MISSING"))
            problems.append(f"{go_file}: {go_name} -> {module}::{want} NOT FOUND")
    return rows, problems, notices


def render(rows: list[tuple[str, str, str, str]]) -> str:
    lines = [
        "# Migration map",
        "",
        "Every Go test function and the Python test that covers it. Generated by",
        "`scripts/migration-map.py`; audited by `tests/architecture/test_migration_map.py`,",
        "which fails when a Go test has no Python counterpart and when a name here does not",
        "exist.",
        "",
        "`Note` is empty when the Python test name is the mechanical conversion of the Go",
        "name, `renamed` when a reviewer chose a different one, and `case` when a table-",
        "driven Go test became a parametrised Python test.",
        "",
        "| Go file | Go test | Python test | Note |",
        "|---|---|---|---|",
    ]
    lines.extend(
        f"| `{go_file}` | `{go_test}` | `{python_test}` | {note} |" for go_file, go_test, python_test, note in rows
    )
    lines.append("")
    lines.append("## Python-only coverage")
    lines.append("")
    lines.append("Tests with no Go counterpart. Each is deliberate additional coverage.")
    lines.append("")
    lines.append("| Python test module | Why it exists |")
    lines.append("|---|---|")
    lines.extend(f"| `{module}` | {why} |" for module, why in sorted(PYTHON_ONLY.items()))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go-repository", type=Path, default=DEFAULT_GO_REPOSITORY)
    parser.add_argument("--check", action="store_true", help="fail if the map would change")
    arguments = parser.parse_args()

    if not arguments.go_repository.is_dir():
        print(f"Go repository not found at {arguments.go_repository}", file=sys.stderr)
        return 2

    rows, problems, notices = build(arguments.go_repository)
    content = render(rows)

    if arguments.check:
        current = MAP_PATH.read_text(encoding="utf-8") if MAP_PATH.is_file() else ""
        if current != content:
            print("MIGRATION_MAP.md is out of date; run scripts/migration-map.py", file=sys.stderr)
            return 1
    else:
        MAP_PATH.write_text(content, encoding="utf-8")

    print(f"{len(rows)} Go tests mapped")
    for problem in problems:
        print(f"  UNMAPPED {problem}")
    for notice in notices:
        print(f"  renamed  {notice}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
