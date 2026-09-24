"""The interactive CLI uses only the terminal ANSI palette."""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERFACE_ROOT = REPO_ROOT / "super_agent" / "tui"
ALLOWED = frozenset({"default", "cyan", "green", "red", "magenta"})
FORBIDDEN_LITERAL = re.compile(r"(?:#[0-9a-fA-F]{3,8}|(?:rgb|rgba|hsl|hsla|color)\s*\()")


def _colour_keywords(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg not in {"color", "bgcolor"}:
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                found.append((keyword.value.lineno, keyword.value.value))
    return found


def test_interface_constructs_only_allowed_terminal_colours() -> None:
    violations: list[str] = []
    visited = 0
    for path in sorted(INTERFACE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        visited += 1
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for line, colour in _colour_keywords(tree):
            if colour not in ALLOWED:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{line}: {colour}")
        for match in FORBIDDEN_LITERAL.finditer(source):
            line = source.count("\n", 0, match.start()) + 1
            violations.append(f"{path.relative_to(REPO_ROOT)}:{line}: {match.group(0)}")
    assert visited > 0
    assert not violations, "non-terminal colours:\n" + "\n".join(violations)


def test_full_screen_framework_is_absent() -> None:
    imports: list[str] = []
    for path in sorted(INTERFACE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(
                    alias.name for alias in node.names if alias.name == "textual" or alias.name.startswith("textual.")
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module is not None
                and (node.module == "textual" or node.module.startswith("textual."))
            ):
                imports.append(node.module)
    assert not imports
    assert not list(INTERFACE_ROOT.rglob("*.tcss"))
