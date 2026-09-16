"""The prompt input, its history, queued follow-ups, and the slash palette.

Ported from the Go ``tui/composer/model.go``. Go embeds ``bubbles/textarea``; the
port keeps the same observable behaviour with a plain value-plus-cursor buffer,
because the editing surface the root actually relies on is small: insert, delete,
move, and the history stack.

One Go signature changes shape: ``Update`` returns ``(Model, Intent | None)``.
Go also returns a ``tea.Cmd`` from the textarea's cursor blink, which has no
Python counterpart, so this feature has no asynchronous effect to hand back.
"""

from __future__ import annotations

import dataclasses
import enum

from rich.style import Style
from rich.text import Text

#: The prompt and selected-row glyphs: U+276F and U+203A.
_PROMPT_GLYPH, _SELECTED_MARKER = "\u276f", "\u203a"
#: The cursor is a reversed cell, which keeps every line the same width.
_CURSOR = Style(reverse=True)
#: Go builds these inline with lipgloss; a feature may not reach for the root's
#: styles (R6), so the composer keeps its own palette.
_ACCENT = Style(color="color(6)", italic=True)
_ACCENT_SELECTED = Style(color="color(6)", bold=True)
_DIM = Style(color="color(8)", italic=True)
_PROMPT = Style(color="color(6)", bold=True)

__all__ = ["Command", "Intent", "IntentKind", "Model", "New", "NormalizeCommands"]

#: How many lines of a long prompt stay visible, matching the textarea's height.
_MAX_VISIBLE_LINES = 5
#: How many queued prompts the footer previews before summarising the rest.
_QUEUE_PREVIEW = 3
#: The palette's visible row count, full and compact.
_PALETTE_ROWS, _PALETTE_ROWS_COMPACT = 6, 3
#: The longest queue preview before it is elided.
_PREVIEW_LIMIT = 72
#: The palette's description column width.
_DESCRIPTION_COLUMN = 17


@dataclasses.dataclass(frozen=True, slots=True)
class Command:
    """One palette entry: the name the composer completes and the hint it shows."""

    Name: str = ""
    Description: str = ""


class IntentKind(enum.Enum):
    """What the composer asks the root to do with the submitted text."""

    NoIntent = "no-intent"
    Submit = "submit"
    Queue = "queue"
    Steer = "steer"
    Clear = "clear"


@dataclasses.dataclass(frozen=True, slots=True)
class Intent:
    """One request from the composer to the root."""

    Kind: IntentKind = IntentKind.NoIntent
    Text: str = ""


@dataclasses.dataclass(slots=True)
class Model:
    """The composer's state: the draft, its history, and the queued follow-ups."""

    commands: tuple[Command, ...]
    value: str = ""
    cursor: int = 0
    history: list[str] = dataclasses.field(default_factory=list[str])
    historyIndex: int = 0
    historyDraft: str = ""
    queued: list[str] = dataclasses.field(default_factory=list[str])
    selection: int = 0
    turnRunning: bool = False
    compactPalette: bool = False
    width: int = 76

    def Init(self) -> None:
        """Nothing to start. Go blinks the textarea's cursor; Rich does not."""
        return None

    def SetWidth(self, width: int) -> None:
        """The terminal got narrower or wider."""
        self.width = max(1, width - 4)

    def SetCompactPalette(self, compact: bool) -> None:
        """Short terminals show palette names without descriptions."""
        self.compactPalette = compact

    def SetTurnRunning(self, running: bool) -> None:
        """Whether Enter steers and Tab queues instead of submitting."""
        self.turnRunning = running

    def Value(self) -> str:
        """The current draft."""
        return self.value

    def ClearInput(self) -> None:
        """Empty the draft."""
        self.value = ""
        self.cursor = 0

    def ClearQueue(self) -> None:
        """Drop every queued follow-up."""
        self.queued = []

    def Enqueue(self, text: str) -> None:
        """Append a follow-up to the queue."""
        self.queued.append(text)

    def Prepend(self, text: str) -> None:
        """Put a steering prompt at the head of the queue."""
        self.queued.insert(0, text)

    def NextQueued(self) -> tuple[str, bool]:
        """Take the oldest queued follow-up, if there is one."""
        if not self.queued:
            return "", False
        return self.queued.pop(0), True

    def Update(self, key: str) -> tuple[Model, Intent | None]:
        """Apply one key press.

        Keys are the canonical names the runtime decodes (``enter``, ``tab``,
        ``ctrl+j``, ``alt+o``); a one-character name is that character typed.
        """
        match key:
            case "esc" | "ctrl+u":
                if self.value == "":
                    return self, None
                self.ClearInput()
                self.historyIndex = len(self.history)
                self.historyDraft = ""
                return self, Intent(Kind=IntentKind.Clear)
            case "ctrl+j" | "shift+enter" | "alt+enter":
                self._insert("\n")
                return self, None
            case "tab":
                if self.turnRunning:
                    return self._action(IntentKind.Queue)
                if self.completeSelected() or self.completeUnique():
                    return self, None
            case "up":
                if matches := self.matches():
                    if self.selection > 0:
                        self.selection -= 1
                    return self, None
                if "\n" not in self.value and self.historyIndex > 0:
                    if self.historyIndex == len(self.history):
                        self.historyDraft = self.value
                    self.historyIndex -= 1
                    self._setValue(self.history[self.historyIndex])
                    return self, None
            case "down":
                if matches := self.matches():
                    if self.selection < len(matches) - 1:
                        self.selection += 1
                    return self, None
                if "\n" not in self.value:
                    if self.historyIndex < len(self.history) - 1:
                        self.historyIndex += 1
                        self._setValue(self.history[self.historyIndex])
                        return self, None
                    if self.historyIndex == len(self.history) - 1:
                        self.historyIndex = len(self.history)
                        self._setValue(self.historyDraft)
                        return self, None
            case "enter":
                if not self.turnRunning and self.completeSelected():
                    return self, None
                if self.turnRunning:
                    return self._action(IntentKind.Steer)
                return self._action(IntentKind.Submit)
            case "backspace":
                self._delete(-1)
            case "delete":
                self._delete(1)
            case "left":
                self.cursor = max(0, self.cursor - 1)
            case "right":
                self.cursor = min(len(self.value), self.cursor + 1)
            case "home":
                self.cursor = 0
            case "end":
                self.cursor = len(self.value)
            case _:
                self.selection = 0
                if len(key) == 1:
                    self._insert(key)
        return self, None

    def View(self) -> Text:
        """The queued preview, the palette, and the input box."""
        sections: list[Text] = []
        if queue := self.queueView():
            sections.append(queue)
        if (palette := self.paletteView()) and not self.turnRunning:
            sections.append(palette)
        rule = " " + "─" * max(1, self.width + 2)
        sections.append(self.inputView(rule))
        return _join(sections)

    def inputView(self, rule: str) -> Text:
        """The rule, the draft, and the rule again."""
        rendered = Text(rule)
        for index, line in enumerate(self._visibleLines()):
            rendered.append("\n")
            rendered.append(f" {_PROMPT_GLYPH} " if index == 0 else "   ", style=_PROMPT)
            rendered.append_text(line)
        rendered.append("\n")
        rendered.append(rule)
        return rendered

    def queueView(self) -> Text:
        """The queued follow-ups, bounded and elided."""
        if not self.queued:
            return Text()
        limit = 0 if self.compactPalette else min(_QUEUE_PREVIEW, len(self.queued))
        rendered = Text(f" Queued ({len(self.queued)})", style=_ACCENT)
        for index in range(limit):
            preview = " ".join(self.queued[index].split())
            if len(preview) > _PREVIEW_LIMIT:
                preview = preview[:_PREVIEW_LIMIT] + "…"
            rendered.append("\n")
            rendered.append(f" {index + 1}. {preview}", style=_DIM)
        if remaining := len(self.queued) - limit:
            rendered.append("\n")
            rendered.append(f" … {remaining} more", style=_DIM)
        return rendered

    def paletteView(self) -> Text:
        """The slash-command matches, scrolled to keep the selection visible."""
        matches = self.matches()
        if not matches:
            return Text()
        visible = _PALETTE_ROWS_COMPACT if self.compactPalette else _PALETTE_ROWS
        start = 0
        if self.selection >= visible:
            start = self.selection - visible + 1
        end = min(start + visible, len(matches))
        rendered = Text()
        for index in range(start, end):
            prefix, style = "  ", _DIM
            if index == self.selection:
                prefix, style = _SELECTED_MARKER + " ", _ACCENT_SELECTED
            label = matches[index].Name
            if not self.compactPalette:
                label = f"{label:<{_DESCRIPTION_COLUMN}} {matches[index].Description}"
            if index > start:
                rendered.append("\n")
            rendered.append(prefix + label, style=style)
        return rendered

    def completeSelected(self) -> bool:
        """Complete the highlighted match, if that changes the draft."""
        matches = self.matches()
        if not matches:
            return False
        if self.selection >= len(matches):
            self.selection = len(matches) - 1
        selected = matches[self.selection].Name
        if self.value == selected:
            return False
        self._setValue(selected)
        self.selection = 0
        return True

    def completeUnique(self) -> bool:
        """Complete a lone match, which is what Tab does for ``/ins``."""
        matches = self.matches()
        if len(matches) != 1 or matches[0].Name == self.value:
            return False
        self._setValue(matches[0].Name)
        self.selection = 0
        return True

    def matches(self) -> tuple[Command, ...]:
        """Every palette entry the draft is a prefix of.

        Only a draft that starts with ``/`` and holds no separator is a command
        being typed; anything else is prose.
        """
        value = self.value
        if not value.startswith("/") or any(character in value for character in " \t\n"):
            return ()
        return tuple(command for command in self.commands if command.Name.startswith(value))

    def _action(self, kind: IntentKind) -> tuple[Model, Intent | None]:
        text = self.value.strip()
        if text == "":
            return self, None
        self._remember(text)
        return self, Intent(Kind=kind, Text=text)

    def _remember(self, text: str) -> None:
        if not self.history or self.history[-1] != text:
            self.history.append(text)
        self.historyIndex = len(self.history)
        self.historyDraft = ""

    def _setValue(self, value: str) -> None:
        self.value = value
        self.cursor = len(value)

    def _insert(self, text: str) -> None:
        self.value = self.value[: self.cursor] + text + self.value[self.cursor :]
        self.cursor += len(text)

    def _delete(self, direction: int) -> None:
        if direction < 0:
            if self.cursor == 0:
                return
            self.value = self.value[: self.cursor - 1] + self.value[self.cursor :]
            self.cursor -= 1
            return
        if self.cursor >= len(self.value):
            return
        self.value = self.value[: self.cursor] + self.value[self.cursor + 1 :]

    def _visibleLines(self) -> list[Text]:
        """The draft, at most :data:`_MAX_VISIBLE_LINES` lines, cursor included."""
        if self.value == "":
            return [Text("Ask me anything... (try /help)", style=_DIM)]
        before = self.value[: self.cursor]
        row = before.count("\n")
        column = len(before) - (before.rfind("\n") + 1)
        rendered: list[Text] = []
        for index, line in enumerate(self.value.split("\n")[:_MAX_VISIBLE_LINES]):
            rendered_line = Text(line)
            if index == row:
                rendered_line = Text(line[:column])
                rendered_line.append(line[column : column + 1] or " ", style=_CURSOR)
                rendered_line.append(line[column + 1 :])
            rendered.append(rendered_line)
        return rendered


def NormalizeCommands(commands: tuple[Command, ...] | list[Command]) -> tuple[Command, ...]:
    """A copy of ``commands``, so the palette cannot be mutated by a caller."""
    return tuple(commands)


def New(commands: tuple[Command, ...] | list[Command]) -> Model:
    """Go's ``composer.New``."""
    return Model(commands=NormalizeCommands(commands))


def _join(sections: list[Text]) -> Text:
    rendered = Text()
    for index, section in enumerate(sections):
        if index:
            rendered.append("\n")
        rendered.append_text(section)
    return rendered
