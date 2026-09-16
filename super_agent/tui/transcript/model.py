"""Committed messages, live streaming content, and the expansion flags.

The feature owns the transcript, the streaming message, and the four expansion
flags; the root owns the viewport it renders into and the clipboard the copy
intent reaches.

A view here is composed of :class:`rich.text.Text` fragments, and wrapping is
left to the root's clamp, so a view is a value, not a width-perfect string. Tests
assert the width invariant at the root rather than the exact layout for that
reason.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from typing import Any, Final, Protocol, cast

from rich.style import Style
from rich.text import Text

__all__ = [
    "ROLE_ASSISTANT",
    "Attachment",
    "Intent",
    "MarkdownRenderer",
    "Message",
    "Model",
    "Role",
    "Styles",
    "ToolCall",
    "ToolDisplayGroup",
    "default_styles",
    "extract_code_blocks",
    "new",
    "toolDisplay",
    "toolGroups",
]


class MarkdownRenderer(Protocol):
    """The renderer this feature needs, restated here on purpose.

    A feature may not import a root module (R6), so the feature declares the shape
    it uses and the root's renderer satisfies it structurally.
    """

    def render(self, content: str, width: int) -> Text: ...


@dataclasses.dataclass(frozen=True, slots=True)
class Styles:
    """The styles the transcript renders with."""

    status: Style
    user_label: Style
    tool_label: Style
    thinking: Style
    footer: Style
    markdown_renderer: MarkdownRenderer


def default_styles() -> Styles:
    """The transcript's own defaults, used when a model is built bare."""
    secondary, accent = "color(8)", "color(6)"
    return Styles(
        status=Style(color=accent, italic=True),
        user_label=Style(color="color(2)", bold=True),
        tool_label=Style(color=accent, bold=True),
        thinking=Style(color=secondary, italic=True),
        footer=Style(color=secondary, italic=True),
        markdown_renderer=_PlainMarkdownRenderer(),
    )


class _PlainMarkdownRenderer:
    """No markdown styling at all: the fallback for a bare transcript model."""

    __slots__ = ()

    def render(self, content: str, width: int) -> Text:
        return Text(content)


#: The user prompt glyph, U+276F.
_PROMPT_GLYPH = "\u276f"


class Role(str):
    """Who produced one transcript message."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Role({str.__repr__(self)})"


ROLE_USER: Final[Role] = Role("user")
ROLE_ASSISTANT: Final[Role] = Role("assistant")


@dataclasses.dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool call, exactly as the model emitted it."""

    id: str = ""
    name: str = ""
    input: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Attachment:
    """One file a message carries."""

    name: str = ""
    mime: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Message:
    """One transcript entry. Frozen, so a rendered transcript never mutates."""

    role: Role = ROLE_USER
    content: str = ""
    reasoning_content: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    interrupted: bool = False
    attachments: tuple[Attachment, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class Intent:
    """A copy request, or the reason there is nothing to copy."""

    copy_text: str = ""
    error: str = ""


@dataclasses.dataclass(slots=True)
class Model:
    """The managed transcript and the four expansion flags."""

    welcome: str = ""
    styles: Styles = dataclasses.field(default_factory=default_styles)
    messages: list[Message] = dataclasses.field(default_factory=list[Message])
    streaming: Message | None = None
    busy: bool = False
    width: int = 80
    expandLatestTools: bool = False
    expandAllTools: bool = False
    expandLatestThink: bool = False
    expandAllThink: bool = False

    def set_width(self, width: int) -> None:
        """The terminal got narrower or wider."""
        self.width = max(1, width)

    def replace(self, messages: Sequence[Message]) -> None:
        """Rebuild the transcript from conversation state."""
        self.messages = list(messages)

    def append(self, message: Message) -> None:
        """Commit one message."""
        self.messages.append(message)

    def set_streaming(self, message: Message | None) -> None:
        """Show, or stop showing, the message being streamed."""
        self.streaming = message

    def clear_streaming(self) -> None:
        """Stop showing the streaming message."""
        self.streaming = None

    def set_busy(self, busy: bool) -> None:
        """Whether a turn is running with nothing to show yet."""
        self.busy = busy

    def update(self, key: str) -> tuple[Model, Intent | None, bool]:
        """Apply one key press.

        The boolean reports whether the key belonged to this feature, which is
        what makes the root's dispatch order observable.
        """
        match key:
            case "ctrl+o":
                self.expandLatestTools = not self.expandLatestTools
                self.expandAllTools = False
                return self, None, True
            case "alt+o":
                self.expandAllTools = not self.expandAllTools
                self.expandLatestTools = False
                return self, None, True
            case "ctrl+t":
                self.expandLatestThink = not self.expandLatestThink
                self.expandAllThink = False
                return self, None, True
            case "alt+t":
                self.expandAllThink = not self.expandAllThink
                self.expandLatestThink = False
                return self, None, True
            case "ctrl+y":
                for message in reversed(self.messages):
                    blocks = extract_code_blocks(message.content)
                    if blocks:
                        return self, Intent(copy_text=blocks[-1]), True
                return self, Intent(error="No code blocks found to copy"), True
            case _:
                return self, None, False

    def view(self) -> Text:
        """The transcript followed by whatever is streaming."""
        parts = [self.transcriptView()]
        if (stream := self.streamingView()) and stream.plain:
            parts.append(stream)
        return _join(parts, "\n\n")

    def transcriptView(self) -> Text:
        """The welcome block and every committed message, in order."""
        latestTool, latestThinking = -1, -1
        for index, message in enumerate(self.messages):
            if message.tool_calls:
                latestTool = index
            if message.role == ROLE_ASSISTANT and message.reasoning_content.strip():
                latestThinking = index
        blocks = [Text(self.welcome)]
        for index, message in enumerate(self.messages):
            toolsExpanded = self.expandAllTools or (self.expandLatestTools and index == latestTool)
            thinkingExpanded = self.expandAllThink or (self.expandLatestThink and index == latestThinking)
            content = self.renderCommitted(message, toolsExpanded, thinkingExpanded)
            if content.plain.strip():
                blocks.append(content)
        return _join(blocks, "\n")

    def streamingView(self) -> Text:
        """The live message, or the compact busy placeholder."""
        styles = self._styles()
        if self.streaming is None:
            if self.busy:
                return Text("Thinking...", style=styles.thinking)
            return Text()
        if self.streaming.content == "" and self.streaming.reasoning_content != "":
            return Text("Thinking...", style=styles.thinking)
        return self.renderMessage(self.streaming, False)

    def renderCommitted(self, message: Message, toolsExpanded: bool, thinkingExpanded: bool) -> Text:
        """One committed message, with its reasoning line and expanded body."""
        content = self.renderMessage(message, toolsExpanded)
        if message.role != ROLE_ASSISTANT:
            return content
        styles = self._styles()
        thinking = Text("Thinking...", style=styles.thinking)
        if thinkingExpanded and message.reasoning_content.strip():
            for index, line in enumerate(message.reasoning_content.strip().split("\n")):
                prefix = "    " if index else "  └ "
                thinking.append("\n")
                thinking.append(prefix + line, style=styles.thinking)
        if not content.plain.strip():
            return thinking
        thinking.append("\n")
        thinking.append_text(content)
        return thinking

    def renderMessage(self, message: Message, toolsExpanded: bool) -> Text:
        """One message body: prose, tool summaries, and attachment lines."""
        styles = self._styles()
        if message.role == ROLE_USER:
            rendered = Text(_PROMPT_GLYPH + " ")
            rendered.stylize(styles.user_label)
            rendered.append(message.content)
            return _indent(rendered, 1)
        rendered = Text()
        if message.role == ROLE_ASSISTANT and message.content != "":
            rendered.append_text(self._renderMarkdown(message.content))
        if message.tool_calls:
            if rendered.plain:
                rendered.append("\n")
            rendered.append_text(self.renderToolCalls(message.tool_calls, toolsExpanded))
        for attachment in message.attachments:
            rendered.append("\n")
            rendered.append(f"  attachment: {attachment.name} ({attachment.mime})", style=styles.status)
        return rendered

    def renderToolCalls(self, calls: Sequence[ToolCall], expanded: bool) -> Text:
        """One summary line per tool group, with its items nested below."""
        styles = self._styles()
        rendered = Text()
        for index, group in enumerate(toolGroups(calls)):
            summary = f"{group.verb} {group.items[0]}"
            if len(group.items) > 1:
                summary = f"{group.verb} {len(group.items)} {group.kind}"
            if index:
                rendered.append("\n")
            rendered.append("● " + summary, style=styles.tool_label)
            if expanded:
                for item_index, item in enumerate(group.items):
                    branch = "└" if item_index == len(group.items) - 1 else "├"
                    rendered.append("\n")
                    rendered.append(f"  {branch} {item}", style=styles.footer)
        return rendered

    def _renderMarkdown(self, content: str) -> Text:
        """Assistant prose through the configured markdown renderer."""
        renderer = self._styles().markdown_renderer
        try:
            return renderer.render(content, max(1, self.width))
        except Exception:
            return Text(content)

    def _styles(self) -> Styles:
        """The feature's styles."""
        return self.styles


@dataclasses.dataclass(frozen=True, slots=True)
class ToolDisplayGroup:
    """One compact action summary and the items it covers."""

    verb: str = ""
    kind: str = ""
    items: tuple[str, ...] = ()


def new(welcome: str, styles: Styles) -> Model:
    """Build the model with its welcome text and styles."""
    return Model(welcome=welcome, styles=styles)


def extract_code_blocks(content: str) -> list[str]:
    """Every fenced code block in ``content``, in order.

    A fence is any line whose trimmed form starts with ````` ``` `````; the
    opening line's language is dropped, an unterminated block contributes
    nothing, and the trailing newline before a closing fence is trimmed.
    """
    blocks: list[str] = []
    current: list[str] = []
    in_block = False
    for line in content.split("\n"):
        if line.strip().startswith("```"):
            if in_block:
                blocks.append("\n".join(current))
                current = []
            in_block = not in_block
            continue
        if in_block:
            current.append(line)
    return blocks


def toolGroups(calls: Sequence[ToolCall]) -> list[ToolDisplayGroup]:
    """Merge consecutive calls with the same verb and kind into one summary."""
    groups: list[ToolDisplayGroup] = []
    for call in calls:
        verb, kind, items = toolDisplay(call)
        if groups and groups[-1].verb == verb and groups[-1].kind == kind:
            previous = groups[-1]
            groups[-1] = dataclasses.replace(previous, items=(*previous.items, *items))
            continue
        groups.append(ToolDisplayGroup(verb=verb, kind=kind, items=tuple(items)))
    return groups


def toolDisplay(call: ToolCall) -> tuple[str, str, list[str]]:
    """The verb, the noun, and the items one tool call contributes."""
    args = _parseArgs(call.input)

    def value(key: str) -> str:
        """A string argument, or the empty string when it is absent."""
        found = args.get(key)
        return found if isinstance(found, str) else ""

    def item(primary: str, fallback: str) -> list[str]:
        """One item, falling back to the tool's own name."""
        return [primary or fallback]

    match call.name:
        case "read_file":
            return "Read", "files", item(value("path"), call.name)
        case "write_file" | "apply_patch":
            return "Edited", "files", item(value("path"), call.name)
        case "go_test":
            packages = _stringSlice(args.get("packages")) or ["./..."]
            return "Ran", "commands", ["go test " + " ".join(packages)]
        case "run_command" | "bash":
            return "Ran", "commands", item(value("command"), call.name)
        case "search" | "web_search":
            return "Searched", "queries", item(value("query"), call.name)
        case "browser_fetch":
            return "Fetched", "pages", item(value("url"), call.name)
        case "list_files":
            return "Listed", "paths", item(value("path"), ".")
        case "format":
            files = _stringSlice(args.get("files")) or [call.name]
            return "Formatted", "files", files
        case "git_status":
            return "Ran", "commands", ["git status --short"]
        case "git_diff":
            return "Ran", "commands", ["git diff"]
        case _:
            return "Called", "tools", [call.name]


def _parseArgs(input_text: str) -> dict[str, Any]:
    """The tool call's JSON arguments, or an empty mapping when there are none.

    Parsing is tolerant: a failure and anything that is not an object both
    normalise to ``{}``.
    """
    try:
        parsed: Any = json.loads(input_text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return cast("dict[str, Any]", parsed)


def _stringSlice(value: object) -> list[str]:
    """Every string in a JSON array, ignoring anything else."""
    if not isinstance(value, list):
        return []
    items = cast("Sequence[Any]", value)
    return [item for item in items if isinstance(item, str)]


def _indent(text: Text, indent: int) -> Text:
    """Pad every line left by ``indent`` columns."""
    if indent <= 0:
        return text
    padding = " " * indent
    rendered = Text()
    for index, line in enumerate(text.split("\n", allow_blank=True)):
        if index:
            rendered.append("\n")
        rendered.append(padding)
        rendered.append_text(line)
    return rendered


def _join(parts: Sequence[Text], separator: str) -> Text:
    """Join rendered fragments, keeping their styles."""
    rendered = Text()
    for index, part in enumerate(parts):
        if index:
            rendered.append(separator)
        rendered.append_text(part)
    return rendered
