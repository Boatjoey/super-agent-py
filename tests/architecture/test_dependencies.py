"""Dependency-rule enforcement.

This module parses each package's imports and fails when the dependency rule in
``docs/architecture.md`` is broken. It also scans the interactive CLI package's
identifiers so a removed full-screen renderer cannot return unannounced.

Rules. ``super_agent`` is the import root, so ``super-agent/tui`` becomes
``super_agent.tui``.

R1  ``tui/**``                       must not import ``super_agent.runtime*``
R2  ``llm/``, ``tools/`` (top level) ``super_agent.runtime*`` may only be ``...protocol``
R3  ``store/``, ``workspace/``,      ``super_agent.runtime*`` may only be ``...protocol``
    ``project/``                     or ``...session``
R4  ``runtime/`` facade (top level)  must not import store, workspace, llm, tools, tui
R5  ``runtime/session/**``           must not import store, tui, ``os``, ``pathlib``
R6  ``tui/<feature>/**``             must not import a sibling feature
R7  ``runtime/machine/**`` (new)     must not import I/O modules or anything outside the pure core
R8  ``tui/<feature>/**`` (new)       must not import the root ``super_agent.tui`` package
R9  ``tui/<feature>/**`` (new)       must not import the composition root ``super_agent.app``

Two Python-shaped adjustments, both narrowing a hole rather than widening a
permission:

* Import paths are matched on package boundaries rather than compared for
  equality (``path == "super-agent/store"``), because a Python submodule such as
  ``super_agent.store.repository`` would otherwise slip past every "concrete
  adapter" rule. The same change keeps ``super_agent.runtime.protocol.types``
  legal under the rule that names ``super-agent/runtime/protocol``.
* The feature rule is an unconditional prefix ban by default. A feature is a
  package whose modules must be able to import each other, so R6 allows the
  file's own feature package and bans everything else under ``super_agent.tui``.
  ``__init__.py`` sits at feature level and is exempt because it belongs to no
  feature.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_NAME = "super_agent"
PACKAGE_ROOT = REPO_ROOT / PACKAGE_NAME
TUI_DIRECTORY = PACKAGE_ROOT / "tui"

PROTOCOL_PACKAGE = "super_agent.runtime.protocol"
SESSION_PACKAGE = "super_agent.runtime.session"
MACHINE_PACKAGE = "super_agent.runtime.machine"

#: Modules ``runtime/machine`` may reach besides its own package. ``runtime.protocol``
#: and ``runtime.permission`` are its declared neighbours; ``errors`` and
#: ``jsonutil`` are pure value and encoding helpers with no I/O and no adapter
#: knowledge, which is the property R7 exists to protect.
PURE_CORE_ALLOWLIST = frozenset(
    {
        PROTOCOL_PACKAGE,
        "super_agent.runtime.permission",
        "super_agent.errors",
        "super_agent.jsonutil",
    }
)

#: Adapters the ``runtime`` facade must not know about (R4).
CONCRETE_ADAPTERS = ("store", "workspace", "llm", "tools", "tui")

#: Standard-library modules that would give the pure core an imperative shell (R7).
IO_MODULES = (
    "asyncio",
    "http",
    "io",
    "os",
    "pathlib",
    "socket",
    "sqlite3",
    "subprocess",
    "threading",
    "time",
)


@dataclass(frozen=True)
class SourceImport:
    """One import statement, resolved to the dotted modules it pulls in."""

    line: int
    modules: tuple[str, ...]


@dataclass(frozen=True)
class Violation:
    file: str
    line: int
    imported: str
    rule: str
    message: str

    def __str__(self) -> str:
        return f'{self.file}:{self.line}: imports "{self.imported}": {self.rule}: {self.message}'


def _within(name: str, package: str) -> bool:
    """True when ``name`` is ``package`` or a submodule of it."""
    return name == package or name.startswith(package + ".")


def _module_index(package_root: Path, package_name: str) -> set[str]:
    """Dotted names of every importable module under ``package_root``.

    ``tui/commands/model.py`` becomes ``super_agent.tui.commands.model`` and
    ``tui/commands/__init__.py`` becomes ``super_agent.tui.commands``. The index
    is what lets ``from X import y`` be read precisely: ``y`` is a submodule only
    when ``X.y`` actually exists, so ``from ...protocol import Message`` is not
    mistaken for an import of ``...protocol.Message``.
    """
    index: set[str] = {package_name}
    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(package_root.parent).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            index.add(".".join(parts))
    return index


def _package_of(path: Path, package_root: Path, package_name: str) -> str:
    """The dotted package that contains ``path``, in the sense of ``__package__``."""
    parts = list(path.relative_to(package_root).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    else:
        parts.pop()
    return ".".join([package_name, *parts]) if parts else package_name


def _resolve_relative(package: str, level: int, module: str | None) -> str | None:
    """Resolve ``from . / .. import`` to a dotted absolute name."""
    if level == 0:
        return module
    parts = package.split(".")
    trimmed = len(parts) - (level - 1)
    if trimmed < 1:
        return None
    base = parts[:trimmed]
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _imports_of(path: Path, package_root: Path, package_name: str, index: set[str]) -> list[SourceImport]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _package_of(path, package_root, package_name)
    found: list[SourceImport] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.append(SourceImport(line=node.lineno, modules=tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_relative(package, node.level, node.module)
            if base is None:
                continue
            modules = [base]
            modules.extend(f"{base}.{alias.name}" for alias in node.names if f"{base}.{alias.name}" in index)
            found.append(SourceImport(line=node.lineno, modules=tuple(modules)))
    return found


def _feature_directories(tui_root: Path) -> list[Path]:
    if not tui_root.is_dir():
        return []
    return sorted(entry for entry in tui_root.iterdir() if entry.is_dir() and entry.name != "__pycache__")


def check_dependencies(repo_root: Path) -> tuple[list[Violation], int]:
    """Return every rule violation under ``repo_root`` and the number of files visited.

    The visited count is returned so callers can prove the walker saw files; a
    rule that silently matches nothing is the failure mode this guards against.
    """
    package_root = repo_root / PACKAGE_NAME
    index = _module_index(package_root, PACKAGE_NAME)

    violations: list[Violation] = []
    visited = 0

    for path in sorted(package_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        visited += 1
        parts = path.relative_to(repo_root).parts
        relative = path.relative_to(repo_root).as_posix()
        top = parts[1] if len(parts) > 1 else ""
        nested = parts[2:-1]
        in_tui = top == "tui"
        # A feature is a directory under tui/, so the file must sit at least one
        # level deeper than tui/ itself.
        feature = parts[2] if in_tui and len(parts) > 3 else None

        for statement in _imports_of(path, package_root, PACKAGE_NAME, index):
            for name in statement.modules:
                for rule, message in _rule_violations(name, top=top, nested=nested, in_tui=in_tui, feature=feature):
                    violations.append(
                        Violation(
                            file=relative,
                            line=statement.line,
                            imported=name,
                            rule=rule,
                            message=message,
                        )
                    )

    return violations, visited


def _rule_violations(
    name: str,
    *,
    top: str,
    nested: tuple[str, ...],
    in_tui: bool,
    feature: str | None,
) -> list[tuple[str, str]]:
    """Apply every rule to one imported module name for one file."""
    found: list[tuple[str, str]] = []
    top_level_file = not nested

    # R1: the interactive CLI is an inbound adapter; it talks to its port.
    if in_tui and _within(name, "super_agent.runtime"):
        found.append(("R1", "interactive CLI must depend on its Conversation port, not runtime"))

    # R2 and R3: top-level adapters see runtime ports, never the machine or engine.
    if (
        top_level_file
        and top in ("llm", "tools")
        and _within(name, "super_agent.runtime")
        and not _within(name, PROTOCOL_PACKAGE)
    ):
        found.append(("R2", f"{top} may depend only on runtime ports"))
    if top_level_file and top in ("store", "workspace", "project"):
        ports = (_within(name, PROTOCOL_PACKAGE), _within(name, SESSION_PACKAGE))
        if _within(name, "super_agent.runtime") and not any(ports):
            found.append(("R3", f"{top} may depend only on runtime ports"))

    # R4: the runtime facade must not know the adapters that wrap it.
    if (
        top == "runtime"
        and top_level_file
        and any(_within(name, f"super_agent.{adapter}") for adapter in CONCRETE_ADAPTERS)
    ):
        found.append(("R4", "runtime facade must not depend on concrete adapters"))

    # R5: the session use-case layer reaches storage and the filesystem through ports.
    if top == "runtime" and nested[:1] == ("session",):
        if _within(name, "super_agent.store") or _within(name, "super_agent.tui"):
            found.append(("R5", "session must use repository and workspace ports"))
        if _within(name, "os") or _within(name, "pathlib"):
            found.append(("R5", "session must use repository and workspace ports"))

    # R6: features collaborate through typed messages.
    if feature is not None and _within(name, "super_agent.tui") and not _within(name, f"super_agent.tui.{feature}"):
        found.append(("R6", "CLI features must collaborate through typed messages, not feature imports"))

    # R7: the machine is the pure core.
    if top == "runtime" and nested[:1] == ("machine",):
        if any(_within(name, module) for module in IO_MODULES):
            found.append(("R7", "runtime/machine must perform no I/O"))
        elif name.startswith("super_agent.") and not (
            _within(name, MACHINE_PACKAGE) or any(_within(name, allowed) for allowed in PURE_CORE_ALLOWLIST)
        ):
            found.append(("R7", "runtime/machine may depend only on the pure core"))

    # R8: features never reach back for the root tui package. R6 covers this too;
    # R8 exists so the invariant has its own name and its own failure message.
    if feature is not None and name == "super_agent.tui":
        found.append(("R8", "CLI features must not import the root tui package"))

    # R9: the composition root wires the TUI, so a feature never reaches back for
    # it. ``app`` imports ``tui``, never the other way round.
    if feature is not None and _within(name, "super_agent.app"):
        found.append(("R9", "CLI features must not import the composition root"))

    return found


def _format(violations: list[Violation]) -> str:
    return "\n".join(str(violation) for violation in violations)


def test_dependency_rule() -> None:
    violations, visited = check_dependencies(REPO_ROOT)
    assert visited > 0, "no Python sources were visited; the walker is broken"
    assert not violations, f"dependency rule violated:\n{_format(violations)}"


def test_tui_feature_rule_sees_files() -> None:
    """R6 and R8 must have something to inspect, or they pass on an empty set."""
    features = _feature_directories(TUI_DIRECTORY)
    assert features, f"no TUI feature directories under {TUI_DIRECTORY}"

    inspected = sum(1 for feature in features for path in feature.rglob("*.py") if "__pycache__" not in path.parts)
    assert inspected > 0, "TUI feature directories contain no Python sources for R6 and R8 to inspect"

    violations, _ = check_dependencies(REPO_ROOT)
    assert not [v for v in violations if v.rule in {"R6", "R8"}], _format(violations)


def test_tui_features_do_not_import_each_other() -> None:
    """The subject: features collaborate through typed messages.

    R6 and R8 are the two rules that carry the invariant, so this asserts on
    their result specifically rather than on the whole rule set.
    """
    features = _feature_directories(TUI_DIRECTORY)
    assert features, f"no TUI feature directories under {TUI_DIRECTORY}"

    violations, _ = check_dependencies(REPO_ROOT)
    feature_violations = [violation for violation in violations if violation.rule in {"R6", "R8"}]
    assert not feature_violations, _format(feature_violations)


def _write_tree(tmp_path: Path, files: dict[str, str]) -> None:
    for relative, body in files.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


#: A tree that satisfies every rule. Each violation case below adds exactly one
#: offending file to a copy of it, so a failure names one rule rather than a
#: missing directory.
_RULE_ABIDING_TREE = {
    "super_agent/__init__.py": "",
    "super_agent/errors.py": "",
    "super_agent/jsonutil.py": "",
    "super_agent/runtime/__init__.py": "",
    "super_agent/runtime/protocol/__init__.py": "",
    "super_agent/runtime/permission/__init__.py": "",
    "super_agent/runtime/machine/__init__.py": "",
    "super_agent/runtime/machine/state.py": "from super_agent.runtime.machine import transition\n",
    "super_agent/runtime/session/__init__.py": "",
    "super_agent/runtime/session/turn.py": "from super_agent.runtime.session import repository\n",
    "super_agent/tui/__init__.py": "",
    "super_agent/tui/commands/__init__.py": "",
    "super_agent/tui/commands/ports.py": "",
    "super_agent/tui/commands/model.py": "from super_agent.tui.commands import ports\n",
    "super_agent/tui/composer/__init__.py": "",
    "super_agent/llm/__init__.py": "",
    "super_agent/tools/__init__.py": "",
    "super_agent/store/__init__.py": "",
    "super_agent/workspace/__init__.py": "",
    "super_agent/project/__init__.py": "",
}

_VIOLATION_CASES: dict[str, tuple[str, str]] = {
    "R1": ("super_agent/tui/layout.py", "from super_agent.runtime.machine import state"),
    "R2": ("super_agent/llm/factory.py", "from super_agent.runtime.engine import engine"),
    "R3": ("super_agent/store/store.py", "from super_agent.runtime.engine import engine"),
    "R4": ("super_agent/runtime/api_engine.py", "from super_agent.store import store"),
    "R5-os": ("super_agent/runtime/session/turn.py", "import os"),
    "R5-pathlib": ("super_agent/runtime/session/turn.py", "from pathlib import Path"),
    "R6": ("super_agent/tui/commands/model.py", "from super_agent.tui.composer import model"),
    "R7-io": ("super_agent/runtime/machine/transition.py", "import asyncio"),
    "R7-core": ("super_agent/runtime/machine/transition.py", "from super_agent.tools import registry"),
    "R8": ("super_agent/tui/commands/model.py", "from super_agent import tui"),
    "R9": ("super_agent/tui/commands/model.py", "from super_agent.app import tui_adapter"),
}


def test_clean_tree_reports_no_violations(tmp_path: Path) -> None:
    """The positive control: a rule-abiding tree produces nothing."""
    _write_tree(tmp_path, _RULE_ABIDING_TREE)
    violations, visited = check_dependencies(tmp_path)
    assert visited == len(_RULE_ABIDING_TREE)
    assert not violations, _format(violations)


def test_rules_reject_synthetic_violations(tmp_path: Path) -> None:
    """Every rule must bite. A rule that never fires is indistinguishable from none."""
    for label, (relative, body) in _VIOLATION_CASES.items():
        expected = label.split("-")[0]
        case_root = tmp_path / label
        _write_tree(case_root, {**_RULE_ABIDING_TREE, relative: body})

        violations, visited = check_dependencies(case_root)
        assert visited > 0, f"{label}: walker visited no files"
        matched = [v for v in violations if v.rule == expected]
        assert matched, f"{label}: {relative} with {body!r} produced no {expected} violation: {_format(violations)}"


def test_rules_accept_the_corrective_alternative(tmp_path: Path) -> None:
    """Each rejected import has an accepted near-miss, so the rules are not blanket bans."""
    corrections = {
        "R1": ("super_agent/tui/layout.py", "from super_agent.tui.conversation import ConversationPort"),
        "R2": ("super_agent/llm/factory.py", "from super_agent.runtime.protocol.types import Message"),
        "R3": ("super_agent/store/store.py", "from super_agent.runtime.protocol.types import Message"),
        "R4": ("super_agent/runtime/api_engine.py", "from super_agent.runtime.engine import engine"),
        "R5": ("super_agent/runtime/session/turn.py", "from super_agent.runtime.session import repository"),
        "R6": ("super_agent/tui/commands/model.py", "from super_agent.tui.commands import ports"),
        "R7": ("super_agent/runtime/machine/transition.py", "from super_agent.runtime.permission import types"),
        "R8": ("super_agent/tui/commands/model.py", "from super_agent.tui.commands import catalog"),
        "R9": ("super_agent/tui/commands/model.py", "from super_agent.tui.commands import ports"),
    }
    for label, (relative, body) in corrections.items():
        case_root = tmp_path / f"ok-{label}"
        extra = {f"super_agent/tui/{name}.py": "" for name in ("conversation",)}
        extra["super_agent/runtime/engine/__init__.py"] = ""
        extra["super_agent/tui/commands/catalog.py"] = ""
        extra["super_agent/runtime/protocol/types.py"] = ""
        extra["super_agent/runtime/permission/__init__.py"] = ""
        extra["super_agent/runtime/permission/types.py"] = ""
        _write_tree(case_root, {**_RULE_ABIDING_TREE, **extra, relative: body})

        violations, _ = check_dependencies(case_root)
        assert not violations, f"{label}: the corrected import was rejected: {_format(violations)}"


# ---------------------------------------------------------------------------
# The deleted legacy renderer
# ---------------------------------------------------------------------------

#: The vocabulary of deleted full-screen renderers. Finding any of these in the
#: interactive CLI package is a re-introduction, not a coincidence.
LEGACY_TUI_SYMBOLS: Final[frozenset[str]] = frozenset(
    {
        "KeyDecoder",
        "Live",
        "Program",
        "Scrollback",
        "_active_console",
        "_raw_mode",
        "_sigwinch",
        "clampLines",
        "fitDynamicArea",
        "read_keys",
    }
)


@dataclass(frozen=True)
class LegacyUse:
    """One reference to a symbol the deleted legacy renderer owned."""

    file: str
    line: int
    symbol: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: uses the removed symbol {self.symbol}"


def _identifiers(tree: ast.AST) -> list[tuple[str, int]]:
    """Every identifier a parsed module imports, declares, or references.

    Docstrings and comments are deliberately not searched: prose may name the
    renderer it replaced, code may not use it.
    """
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name.rsplit(".", 1)[-1], node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.Attribute):
            found.append((node.attr, node.lineno))
        elif isinstance(node, ast.Name):
            found.append((node.id, node.lineno))
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((node.name, node.lineno))
    return found


def check_legacy_renderer(repo_root: Path) -> tuple[list[LegacyUse], int]:
    """Return every use of the deleted renderer's vocabulary, and the files visited.

    The visited count is returned for the same reason ``check_dependencies``
    returns one: a scan that silently sees nothing is indistinguishable from a
    clean tree.
    """
    uses: list[LegacyUse] = []
    visited = 0

    for path in sorted((repo_root / PACKAGE_NAME / "tui").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        visited += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(repo_root).as_posix()
        for symbol, line in _identifiers(tree):
            if symbol in LEGACY_TUI_SYMBOLS:
                uses.append(LegacyUse(file=relative, line=line, symbol=symbol))

    return uses, visited


def test_legacy_renderer_vocabulary_is_gone() -> None:
    uses, visited = check_legacy_renderer(REPO_ROOT)
    assert visited > 0, "no TUI sources were visited; the scan is broken"
    assert not uses, "the deleted legacy renderer is back:\n" + "\n".join(str(use) for use in uses)


def test_legacy_renderer_scan_rejects_each_removed_symbol(tmp_path: Path) -> None:
    """Every removed symbol must bite: one that never fires is indistinguishable from none."""
    shapes = {
        "use": "{symbol} = None\n",
        "define": "def {symbol}() -> None:\n    return None\n",
        "import": "from rich.live import {symbol}\n",
    }
    for symbol in sorted(LEGACY_TUI_SYMBOLS):
        for shape, body in shapes.items():
            case_root = tmp_path / f"{symbol}-{shape}"
            _write_tree(case_root, {"super_agent/tui/legacy.py": body.format(symbol=symbol)})

            uses, visited = check_legacy_renderer(case_root)
            assert visited == 1, f"{symbol} ({shape}): the scan visited {visited} files"
            assert [use.symbol for use in uses] == [symbol], f"{symbol} ({shape}): {uses!r}"


def test_legacy_renderer_scan_accepts_the_live_vocabulary(tmp_path: Path) -> None:
    """The near-miss: the vocabulary the shell still runs is not a violation."""
    _write_tree(
        tmp_path,
        {
            "super_agent/tui/runtime.py": (
                "Listener = None\nWindowSizeMsg = None\nKeyMsg = None\nbatch = None\nclass Command:\n    pass\n"
            )
        },
    )

    uses, visited = check_legacy_renderer(tmp_path)
    assert visited == 1
    assert not uses, f"the live vocabulary was reported as removed: {uses!r}"
