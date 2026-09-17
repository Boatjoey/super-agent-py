"""The Textual theme: the terminal's own palette, named rather than constructed.

``docs/tui.md#appearance`` gives the interface six colour roles and no theme to
pick. This module is where those roles meet Textual: the theme names ANSI
colours, and ``ansi=True`` makes the application's ``native_ansi_color`` true,
which switches off Textual's ANSI-to-truecolour filter so the names leave the
process as escapes and resolve against whatever palette the terminal is
configured with.

Textual's own ANSI defaults are not palette-clean. Its scrollbars and links are
blue, its border is magenta, and its cursors and borders reach for black; a
theme that only named the six roles would ship all of them. ``Theme.variables``
is merged over those defaults, so every offending one is overridden here, and
``tests/architecture/test_theme.py`` mounts the application and reads the
resolved variables back to prove it still holds.
"""

from __future__ import annotations

from textual.theme import Theme

__all__ = ["NAME", "THEME"]

#: The name the application registers the theme under and selects it by.
NAME = "super-agent"

#: The terminal's default foreground and background.
_DEFAULT = "ansi_default"
#: User input, selection, and status indicators.
_ACCENT = "ansi_cyan"
#: The agent's identity marker.
_IDENTITY = "ansi_magenta"
#: Success and added lines.
_SUCCESS = "ansi_green"
#: Errors, failures, and removed lines.
_FAILURE = "ansi_red"

#: The variables Textual's stylesheet reads but does not derive from a theme
#: colour. The first two are referenced by Textual's own base stylesheet, so the
#: stylesheet does not parse without them; the rest override ANSI-mode defaults
#: that name a colour the palette does not have.
_VARIABLES: dict[str, str] = {
    # Blue in Textual's defaults, where the accent is cyan.
    "scrollbar": _ACCENT,
    "scrollbar-hover": _ACCENT,
    "scrollbar-active": _ACCENT,
    "link-color": _ACCENT,
    "link-color-hover": _ACCENT,
    "link-background-hover": _ACCENT,
    # Magenta in Textual's defaults, which the palette reserves for the agent.
    "border": _ACCENT,
    "block-cursor-background": _ACCENT,
    "footer-key-foreground": _ACCENT,
    # Black or white in Textual's defaults; the caret stays cyan so the cursor is
    # visible against the terminal's own background.
    "ansi-background": _DEFAULT,
    "ansi-foreground": _DEFAULT,
    "border-blurred": _DEFAULT,
    "scrollbar-background": _DEFAULT,
    "scrollbar-background-hover": _DEFAULT,
    "scrollbar-background-active": _DEFAULT,
    "block-cursor-foreground": _DEFAULT,
    "input-cursor-background": _ACCENT,
    "input-cursor-foreground": _DEFAULT,
    "input-selection-background": _ACCENT,
    "input-selection-foreground": _DEFAULT,
    "screen-selection-background": _ACCENT,
    "screen-selection-foreground": _DEFAULT,
    # Headings carry no role of their own: body text is the terminal default.
    "markdown-h1-color": _DEFAULT,
    "markdown-h2-color": _DEFAULT,
    "markdown-h3-color": _DEFAULT,
    "markdown-h4-color": _DEFAULT,
    "markdown-h5-color": _DEFAULT,
    "markdown-h6-color": _DEFAULT,
    # Muted text is the default foreground, dimmed where the interface draws it;
    # a variable cannot carry a text style, so it stays the plain colour here.
    "text-muted": _DEFAULT,
    "foreground-muted": _DEFAULT,
}

THEME = Theme(
    name=NAME,
    ansi=True,
    primary=_ACCENT,
    secondary=_IDENTITY,
    accent=_ACCENT,
    success=_SUCCESS,
    error=_FAILURE,
    warning=_FAILURE,
    foreground=_DEFAULT,
    background=_DEFAULT,
    surface=_DEFAULT,
    panel=_DEFAULT,
    boost=_DEFAULT,
    dark=True,
    variables=_VARIABLES,
)
