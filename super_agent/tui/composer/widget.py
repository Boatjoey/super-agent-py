"""The prompt editor widget.

Textual drops an application binding for any key the focused widget would capture,
so a binding on a printable character only exists if the widget says the character
is not exclusively its own. `?` is the one such key: it opens help while the
prompt is empty and is an ordinary character otherwise.

Editing, selection, cursor movement, paste, and input-method handling stay with
the text area; the draft, the history, the queue, and the palette belong to the
feature model beside this widget.
"""

from __future__ import annotations

from textual.widgets import TextArea

__all__ = ["Composer"]


class Composer(TextArea):
    """The multiline prompt editor."""

    def check_consume_key(self, key: str, character: str | None = None) -> bool:
        """Whether this widget may capture ``key`` before the application sees it."""
        if key == "question_mark":
            return False
        return super().check_consume_key(key, character)
