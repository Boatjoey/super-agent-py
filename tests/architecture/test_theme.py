"""Colour enforcement: the interface draws from the terminal's palette.

``docs/tui.md#appearance`` fixes the whole vocabulary — the terminal's default
foreground, that foreground dimmed, and ANSI cyan, green, red, and magenta — and
rules out everything else: no hexadecimal literal, no ``rgb(...)`` triple, no
indexed ``color(N)`` entry, and no colour name outside those roles. A named
syntax-highlighting theme is the one exception, because it is selected by name
from ``tui.syntax_theme`` and not defined here; nothing below looks at a
``theme=`` or ``code_theme=`` argument, so that exemption is structural rather
than accidental. ``test_named_syntax_theme_is_not_a_colour`` pins it down.

Two instruments, because the rule has two shapes.

* :func:`colour_violations` parses the TUI's own sources with :mod:`ast` and
  reads the *construction sites* — ``Style(...)``, ``Color(...)``,
  ``Text(..., style=...)``, ``.stylize(...)``, ``Theme(...)`` — so a colour
  written in a comment or inside a markdown sample is not a false positive.
  Stylesheets are read as ``color``/``background``/``border`` declarations,
  whether they live in a ``.tcss`` file or in a ``DEFAULT_CSS`` string.
* The mounted application is asked what it actually resolved. Textual's own ANSI
  defaults are blue scrollbars and links, a magenta border, and black cursors,
  and only :mod:`super_agent.tui.theme`'s overrides keep them off the screen;
  the source scan cannot see those because they are not our source. The mounted
  tests fail when an override is dropped.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

import pytest
from rich.color import ANSI_COLOR_NAMES, Color as RichColor, ColorType
from textual.app import App as TextualApp, ComposeResult
from textual.color import Color as TextualColor
from textual.containers import VerticalScroll
from textual.widgets import Static

from super_agent.tui import theme

REPO_ROOT = Path(__file__).resolve().parents[2]
TUI_DIRECTORY = REPO_ROOT / "super_agent" / "tui"

#: The roles of ``docs/tui.md#appearance``, in Rich's spelling and in Textual's.
PALETTE = frozenset(
    {
        "default",
        "transparent",
        "cyan",
        "bright_cyan",
        "green",
        "bright_green",
        "red",
        "bright_red",
        "magenta",
        "bright_magenta",
    }
)

#: The names the specification calls out by name, so a failure can say why.
FOREGROUND_BAN = frozenset(
    {"black", "bright_black", "white", "bright_white", "blue", "bright_blue", "yellow", "bright_yellow"}
)

#: ``color(N)``: the indexed 256-colour form.
_INDEXED = re.compile(r"color\(\s*\d+\s*\)")
#: ``#rgb``, ``#rgba``, ``#rrggbb``, ``#rrggbbaa``.
_HEX = re.compile(r"#[0-9a-f]{3,8}")
#: ``rgb(...)``, ``rgba(...)``, ``hsl(...)``, ``hsla(...)``.
_FUNCTION = re.compile(r"(?:rgba?|hsla?)\(")
#: A CSS declaration, which is enough of one line to read a colour out of.
_DECLARATION = re.compile(r"(?P<property>[a-z-]+)\s*:\s*(?P<value>[^;{}]*)")
#: A CSS comment, whose example of a bad declaration is not one.
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)

#: The CSS properties whose values can name a colour.
_COLOUR_PROPERTIES = frozenset(
    {
        "color",
        "background",
        "background-color",
        "background-tint",
        "border",
        "border-top",
        "border-right",
        "border-bottom",
        "border-left",
        "border-color",
        "outline",
        "keyline",
        "scrollbar-color",
        "scrollbar-color-hover",
        "scrollbar-color-active",
    }
)

#: The properties above that paint a background rather than a foreground.
_BACKGROUND_PROPERTIES = frozenset({"background", "background-color", "background-tint"})

#: The keyword arguments that name a colour, and whether each paints a foreground.
_COLOUR_KEYWORDS = {"color": True, "bgcolor": False, "background": False, "foreground": True}

#: The calls whose arguments are colours. A syntax theme is named through none of
#: them, which is what keeps ``tui.syntax_theme`` out of this rule.
_COLOUR_CALLS = frozenset({"Style", "Color", "ColorTriplet", "Text", "Theme", "stylize", "append"})

#: The ``Theme`` keyword arguments that name a colour.
_THEME_COLOUR_FIELDS = frozenset(
    {
        "primary",
        "secondary",
        "accent",
        "foreground",
        "background",
        "surface",
        "panel",
        "boost",
        "success",
        "warning",
        "error",
    }
)

#: ``Theme`` keyword arguments that paint a background.
_THEME_BACKGROUNDS = frozenset({"background", "surface", "panel", "boost"})

#: The ANSI palette by index, as Rich numbers it when it renders.
_ANSI_NUMBERS = {
    0: "black",
    1: "red",
    2: "green",
    3: "yellow",
    4: "blue",
    5: "magenta",
    6: "cyan",
    7: "white",
    8: "bright_black",
    9: "bright_red",
    10: "bright_green",
    11: "bright_yellow",
    12: "bright_blue",
    13: "bright_magenta",
    14: "bright_cyan",
    15: "bright_white",
}


@dataclasses.dataclass(frozen=True)
class Violation:
    """One colour named outside the palette."""

    file: str
    line: int
    value: str
    context: str
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.context} names {self.value!r}: {self.message}"


def _named(token: str) -> str:
    """The colour name without Textual's ``ansi_`` prefix."""
    return token.lower().removeprefix("ansi_")


def _is_colour(token: str) -> bool:
    """Whether the token names a colour at all, as opposed to a style attribute.

    Textual parses its own CSS colours and Rich parses its style colours; a token
    neither of them knows (``bold``, ``round``, ``1fr``) is not a colour.
    """
    if _HEX.fullmatch(token) or _INDEXED.fullmatch(token) or _FUNCTION.match(token):
        return True
    if _named(token) in PALETTE or token.lower() in ANSI_COLOR_NAMES:
        return True
    try:
        TextualColor.parse(token)
    except Exception:
        return False
    return True


def _message(value: str, *, foreground: bool) -> str | None:
    """Why ``value`` is not a palette colour, or ``None`` when it is one."""
    token = value.strip()
    if _HEX.fullmatch(token) or _INDEXED.fullmatch(token) or _FUNCTION.match(token):
        return "a literal colour; the palette names terminal colours by role"
    name = _named(token)
    if name in PALETTE or not _is_colour(token):
        return None
    if foreground and name in FOREGROUND_BAN:
        return "barred as a foreground by docs/tui.md#appearance"
    return "not one of the six roles in docs/tui.md#appearance"


def _style_colours(value: str) -> list[tuple[str, bool]]:
    """The colours of one Rich style string, each with the role it paints.

    A style string carries a colour among its attributes (``"bold cyan"``), and
    ``on`` switches the rest of it to the background (``"red on blue"``).
    """
    found: list[tuple[str, bool]] = []
    foreground = True
    for token in value.split():
        if token.lower() == "on":
            foreground = False
        elif _is_colour(token):
            found.append((token, foreground))
    return found


def _string(node: ast.expr | None) -> str | None:
    """The literal string ``node`` holds, or ``None`` when it holds something else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.expr) -> str:
    """The name a call goes by: ``Style`` for ``Style(...)`` and ``stylize`` for ``text.stylize(...)``."""
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _found(path: str, line: int, value: str, *, foreground: bool, context: str) -> list[Violation]:
    """A violation for one colour argument, if it is one."""
    message = _message(value, foreground=foreground)
    if message is None:
        return []
    return [Violation(file=path, line=line, value=value, context=context, message=message)]


def _keyword_colours(node: ast.Call, path: str) -> list[Violation]:
    """The colours a call names in its keyword arguments."""
    name = _call_name(node.func)
    found: list[Violation] = []
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        argument = keyword.arg
        if argument == "variables":
            found.extend(_variable_colours(keyword.value, path, node.lineno))
            continue
        if name == "Theme" and argument not in _THEME_COLOUR_FIELDS:
            continue
        if name != "Theme" and argument != "style" and argument not in _COLOUR_KEYWORDS:
            continue
        text = _string(keyword.value)
        if text is None:
            continue
        context = f"{name}({argument}=)"
        if argument == "style":
            for value, foreground in _style_colours(text):
                found.extend(_found(path, node.lineno, value, foreground=foreground, context=context))
            continue
        foreground = _COLOUR_KEYWORDS.get(argument, argument not in _THEME_BACKGROUNDS)
        found.extend(_found(path, node.lineno, text, foreground=foreground, context=context))
    return found


def _variable_colours(node: ast.expr, path: str, line: int) -> list[Violation]:
    """The colours a ``Theme(variables={...})`` mapping names."""
    if not isinstance(node, ast.Dict):
        return []
    found: list[Violation] = []
    for key, value in zip(node.keys, node.values, strict=True):
        name = _string(key)
        text = _string(value)
        if name is None or text is None:
            continue
        foreground = not name.endswith("background") and name not in _THEME_BACKGROUNDS
        found.extend(_found(path, line, text, foreground=foreground, context=f"variables[{name!r}]"))
    return found


def _positional_colours(node: ast.Call, path: str) -> list[Violation]:
    """The colours a call names in its positional arguments.

    ``Color(255, 0, 170)`` is a triple; ``Style("bold red")`` is a style string;
    ``Text("prose", "red")`` keeps its first argument as content.
    """
    name = _call_name(node.func)
    found: list[Violation] = []
    numbers = [
        argument.value
        for argument in node.args
        if isinstance(argument, ast.Constant) and isinstance(argument.value, int | float)
    ]
    if name in {"Color", "ColorTriplet"} and len(numbers) >= 3:
        triple = ", ".join(str(number) for number in numbers[:3])
        found.append(
            Violation(
                file=path,
                line=node.lineno,
                value=f"{name}({triple})",
                context=f"{name}(...)",
                message="a colour from an RGB triple; the palette names terminal colours by role",
            )
        )
    styles = node.args[1:] if name == "Text" else node.args
    if name == "stylize":
        styles = node.args[:1]
    for argument in styles:
        text = _string(argument)
        if text is None:
            continue
        for value, foreground in _style_colours(text):
            found.extend(_found(path, node.lineno, value, foreground=foreground, context=f"{name}(...)"))
    return found


def _stylesheet_violations(text: str, *, path: str, offset: int = 0) -> list[Violation]:
    """Literal colours in the ``color``/``background``/``border`` declarations of a stylesheet."""
    found: list[Violation] = []
    for number, line in enumerate(_COMMENT.sub("", text).splitlines(), start=1):
        for declaration in _DECLARATION.finditer(line):
            prop = declaration.group("property").lower()
            if prop not in _COLOUR_PROPERTIES:
                continue
            foreground = prop not in _BACKGROUND_PROPERTIES
            for token in declaration.group("value").replace(",", " ").split():
                if token.startswith("$"):
                    continue
                found.extend(_found(path, offset + number, token, foreground=foreground, context=f"{prop}:"))
    return found


def _css_string_violations(node: ast.Assign | ast.AnnAssign, path: str) -> list[Violation]:
    """A ``DEFAULT_CSS`` string: a widget's stylesheet, held in a Python module."""
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    if not any(_target_name(target).endswith("CSS") for target in targets):
        return []
    text = _string(node.value)
    if text is None:
        return []
    return _stylesheet_violations(text, path=path, offset=node.lineno - 1)


def _target_name(target: ast.expr) -> str:
    """The name a statement assigns to, for ``X`` and ``X.attr`` alike."""
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Name):
        return target.id
    return ""


def python_violations(tree: ast.AST, *, path: str) -> list[Violation]:
    """Every colour named outside the palette in one parsed module."""
    found: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) in _COLOUR_CALLS:
            found.extend(_keyword_colours(node, path))
            found.extend(_positional_colours(node, path))
        elif isinstance(node, ast.Assign | ast.AnnAssign):
            found.extend(_css_string_violations(node, path))
    return found


def colour_violations(repo_root: Path) -> tuple[list[Violation], int]:
    """Every colour named outside the palette under ``repo_root``, and the files visited.

    The visited count is returned so callers can prove the walker saw files; a
    rule that silently matches nothing is the failure mode this guards against.
    """
    tui = repo_root / "super_agent" / "tui"
    violations: list[Violation] = []
    visited = 0
    for path in sorted(tui.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        visited += 1
        relative = path.relative_to(repo_root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        violations.extend(python_violations(tree, path=relative))
    for path in sorted(tui.rglob("*.tcss")):
        visited += 1
        relative = path.relative_to(repo_root).as_posix()
        violations.extend(_stylesheet_violations(path.read_text(encoding="utf-8"), path=relative))
    return violations, visited


def _format(violations: list[Violation]) -> str:
    return "\n".join(str(violation) for violation in violations)


def _ansi_name(colour: RichColor) -> str:
    """The ANSI name of a rendered colour; anything else is named as itself."""
    if colour.type is ColorType.DEFAULT:
        return "default"
    number = colour.number
    if colour.type in {ColorType.STANDARD, ColorType.EIGHT_BIT} and number is not None:
        return _ANSI_NUMBERS.get(number, f"color({number})")
    # A truecolour renders as its own hex, which the rule reads as a literal.
    return colour.name or "truecolor"


def _drawn_colours(app: TextualApp[None]) -> set[str]:
    """Every colour the compositor would paint, as an ANSI name.

    The strips are Textual's own compositor output, reached through the attribute
    it keeps them in: this is the closest thing to "what the terminal receives"
    that a test can read without a terminal.
    """
    compositor = app.screen._compositor  # pyright: ignore[reportPrivateUsage] - Textual's own render output
    drawn: set[str] = set()
    for strip in compositor.render_strips():
        for segment in strip:
            style = segment.style
            if style is None:
                continue
            for colour in (style.color, style.bgcolor):
                if colour is not None:
                    drawn.add(_ansi_name(colour))
    return drawn


class _Probe(TextualApp[None]):
    """A window onto the theme: a bordered scroll pane, and a line of muted text."""

    CSS = """
    Screen { background: $background; color: $text; }
    #pane { height: 3; border: round $border; scrollbar-gutter: stable; }
    #long { height: 20; }
    #muted { color: $text-muted; text-style: dim; }
    """

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="pane"):
            yield Static("x" * 400, id="long")
        yield Static("hint", id="muted")


def _probe() -> _Probe:
    """The probe with the project's theme registered and selected, as the app does."""
    probe = _Probe()
    probe.register_theme(theme.THEME)
    probe.theme = theme.NAME
    return probe


def test_tui_source_names_only_palette_colours() -> None:
    """The subject: no colour outside the six roles is constructed anywhere in the TUI."""
    violations, visited = colour_violations(REPO_ROOT)
    assert visited > 0, "no TUI sources were visited; the walker is broken"
    assert not violations, f"the TUI names colours outside the palette:\n{_format(violations)}"


def test_walker_sees_every_tui_source() -> None:
    """The scan must have every module and stylesheet to inspect, or it passes on an empty set."""
    modules = [path for path in (TUI_DIRECTORY).rglob("*.py") if "__pycache__" not in path.parts]
    stylesheets = list(TUI_DIRECTORY.rglob("*.tcss"))
    assert modules, f"no Python sources under {TUI_DIRECTORY}"
    assert stylesheets, f"no stylesheets under {TUI_DIRECTORY}"

    _, visited = colour_violations(REPO_ROOT)
    assert visited == len(modules) + len(stylesheets), "the walker skipped a source"


_MODULE = "super_agent/tui/feature/model.py"
_STYLESHEET = "super_agent/tui/feature/theme.tcss"

#: Sources that stay inside the palette: an injected style, a palette name, a
#: comment, a markdown sample, a token-only declaration, and a named syntax theme.
_CLEAN_SOURCES: dict[str, str] = {
    "palette": 'accent = Style(color="cyan", bold=True)\n',
    "default": 'plain = Style(color="default")\nsecondary = Style(dim=True)\n',
    "injected": "rendered.append(text, style=app.styles.accent)\nrendered.stylize(styles.secondary)\n",
    "commented": '# Style(color="color(1)") would be a violation\n',
    "sample": 'HELP = "write it as #ff00aa in a markdown sample"\n',
    "class-css": 'class Output(Static):\n    DEFAULT_CSS = """\n    Output { background: $surface; border: round $accent; }\n    """\n',
    "syntax-theme": 'editor = TextArea(language="python", theme="ansi_dark")\n',
}

#: The same, for a stylesheet: bare tokens and non-colour keywords only.
_CLEAN_STYLESHEETS: dict[str, str] = {
    "tokens": "Screen {\n    color: $text;\n    background: $background;\n}\n",
    "border": "#composer:focus {\n    border: round $border;\n    scrollbar-gutter: stable;\n}\n",
}

#: Sources that each name exactly one colour outside the palette.
_VIOLATION_SOURCES: dict[str, str] = {
    "hex": 'spot = Style(color="#ff00aa")\n',
    "indexed": 'spot = Style(color="color(6)")\n',
    "triplet": "spot = Color(255, 0, 170)\n",
    "rgb-function": 'spot = Style(color="rgb(255, 0, 170)")\n',
    "blue-foreground": 'sea = Style(color="blue")\n',
    "yellow-foreground": 'sun = Text("x", style="ansi_bright_yellow")\n',
    "black-foreground": 'ink = Style(color="ansi_black")\n',
    "white-foreground": 'paper = Text("x")\npaper.stylize("white")\n',
    "off-palette-name": 'warm = Style(color="orange")\n',
    "append-style": 'rendered.append("x", style="#ff00aa")\n',
    "theme-hex": 'THEME = Theme(name="x", primary="#0178D4")\n',
    "theme-variable": 'THEME = Theme(name="x", variables={"scrollbar": "ansi_blue"})\n',
    "class-css": 'class Output(Static):\n    DEFAULT_CSS = """\n    Output { background: #ff00aa; }\n    """\n',
}

#: Stylesheets that each name exactly one colour outside the palette.
_VIOLATION_STYLESHEETS: dict[str, str] = {
    "hex": "#thing {\n    color: #ff00aa;\n}\n",
    "indexed": "#thing {\n    color: color(6);\n}\n",
    "blue-name": "#thing {\n    border: round blue;\n}\n",
    "blue-background": "#thing {\n    background: blue;\n}\n",
    "off-palette-name": "#thing {\n    color: dimgrey;\n}\n",
}


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, body in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")


def test_clean_sources_are_accepted(tmp_path: Path) -> None:
    """The positive control: a source inside the palette produces nothing."""
    cases = {**_CLEAN_SOURCES, **{f"css-{label}": body for label, body in _CLEAN_STYLESHEETS.items()}}
    for label, source in cases.items():
        root = tmp_path / f"ok-{label}"
        _write_tree(root, {(_MODULE if label in _CLEAN_SOURCES else _STYLESHEET): source})
        violations, visited = colour_violations(root)
        assert visited > 0, f"{label}: the walker visited no files"
        assert not violations, f"{label}: {_format(violations)}"


def test_rules_reject_synthetic_violations(tmp_path: Path) -> None:
    """Every rule must bite. A rule that never fires is indistinguishable from none."""
    cases = {**_VIOLATION_SOURCES, **{f"css-{label}": body for label, body in _VIOLATION_STYLESHEETS.items()}}
    for label, source in cases.items():
        root = tmp_path / label
        _write_tree(root, {(_MODULE if label in _VIOLATION_SOURCES else _STYLESHEET): source})

        violations, visited = colour_violations(root)
        assert visited > 0, f"{label}: the walker visited no files"
        assert violations, f"{label}: {source!r} produced no violation"


def test_named_syntax_theme_is_not_a_colour() -> None:
    """The documented exemption, asserted rather than assumed: ``tui.syntax_theme``
    names a theme, and no colour rule reads a theme name."""
    tree = ast.parse('editor = TextArea(language="python", theme="ansi_dark")\n')
    assert python_violations(tree, path="super_agent/tui/syntax.py") == []

    source = dict(_CLEAN_SOURCES)["syntax-theme"]
    for theme_name in ("ansi_dark", "ansi_light", "monokai", "solarized-dark"):
        replaced = source.replace("ansi_dark", theme_name)
        assert python_violations(ast.parse(replaced), path="super_agent/tui/syntax.py") == [], theme_name


def test_imported_colour_helper_is_not_scanned_blindly() -> None:
    """A value that arrives from elsewhere is not a construction site: the palette
    is allowed to hand a style over, which is how features receive one."""
    tree = ast.parse("marker = Text('x', style=styles.identity)\nlabel = Style(color=palette.accent)\n")
    assert python_violations(tree, path="super_agent/tui/transcript/view.py") == []


@pytest.mark.asyncio
async def test_mounted_theme_takes_its_palette_from_ansi() -> None:
    """The mechanism the whole design rests on: ANSI escapes reach the terminal."""
    async with _probe().run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert pilot.app.theme == theme.NAME
        assert pilot.app.current_theme.ansi is True
        assert pilot.app.native_ansi_color is True


@pytest.mark.asyncio
async def test_resolved_variables_stay_on_the_palette() -> None:
    """Textual's own ANSI defaults are not palette-clean, and only the theme's
    overrides keep them out. This fails when one of them is dropped."""
    async with _probe().run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        variables = pilot.app.theme_variables
        assert variables, "the theme resolved no variables"
        for name, value in sorted(variables.items()):
            for token in value.split():
                message = _message(token, foreground=not name.endswith("background"))
                assert message is None, f"${name} resolves to {value!r}: {token!r} {message}"

        # Magenta is the agent's identity marker, so nothing else may paint it.
        magenta = {name for name, value in variables.items() if "ansi_magenta" in value}
        assert magenta, "the identity role resolved to no variable"
        stray = sorted(name for name in magenta if not name.startswith(("secondary", "text-secondary")))
        assert not stray, f"magenta is reserved for the agent's identity marker, but ${{{', $'.join(stray)}}} paint it"


@pytest.mark.asyncio
async def test_border_scrollbar_and_selection_are_painted_from_the_palette() -> None:
    """The resolved styles of a real widget, where Textual's defaults would not be."""
    async with _probe().run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        pane = pilot.app.query_one("#pane", VerticalScroll)
        assert pane.styles.border_top[1].hex == "ansi_cyan"
        assert pane.styles.scrollbar_color.hex == "ansi_cyan"
        assert pane.styles.scrollbar_background.hex == "ansi_default"
        assert pane.styles.scrollbar_color_hover.hex == "ansi_cyan"
        assert pane.styles.scrollbar_color_active.hex == "ansi_cyan"

        # Secondary text is the default foreground, dimmed, and not a grey.
        muted = pilot.app.query_one("#muted", Static)
        assert muted.styles.color.hex == "ansi_default"
        assert muted.styles.text_style.dim is True


@pytest.mark.asyncio
async def test_rendered_segments_stay_on_the_palette() -> None:
    """What the compositor would draw, rather than what the source says."""
    async with _probe().run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        drawn = _drawn_colours(pilot.app)
        assert drawn, "the compositor painted no colour at all"
        barred = sorted(colour for colour in drawn if _message(colour, foreground=True) is not None)
        assert not barred, f"the compositor would paint {barred}, which the palette does not have"
