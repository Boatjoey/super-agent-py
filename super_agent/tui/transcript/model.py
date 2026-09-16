"""Committed messages, live streaming content, and the expansion flags.

Ported from the Go ``tui/transcript/model.go``. The feature owns the transcript,
the streaming message, and the four expansion flags; the root owns the viewport
it renders into and the clipboard the copy intent reaches.

Go composes rendered strings with lipgloss and ``ansi.Wrap``. The port composes
:class:`rich.text.Text` fragments and leaves wrapping to the root's clamp, so a
view here is a value, not a width-perfect string. Tests assert the width
invariant at the root rather than the exact layout for that reason.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from typing import Any, Final, Protocol, cast

from rich.style import Style
from rich.text import Text

__all__ = [
    "Attachment",
    "DefaultStyles",
    "ExtractCodeBlocks",
    "Intent",
    "MarkdownRenderer",
    "Message",
    "Model",
    "New",
    "Role",
    "RoleAssistant",
    "Styles",
    "ToolCall",
    "ToolDisplayGroup",
    "toolDisplay",
    "toolGroups",
]


class MarkdownRenderer(Protocol):
    """The renderer this feature needs, restated here on purpose.

    Go's ``transcript.Styles`` holds a glamour ``TermRenderer``: a type both the
    feature and the root can name because it comes from a library. Rich has no
    such type, and a feature may not import a root module (R6), so the feature
    declares the shape it uses and the root's renderer satisfies it structurally.
    """

    def render(self, content: str, width: int) -> Text: ...


@dataclasses.dataclass(frozen=True, slots=True)
class Styles:
    """The styles the transcript renders with: Go's ``transcript.Styles``."""

    Status: Style
    UserLabel: Style
    ToolLabel: Style
    Thinking: Style
    Footer: Style
    MarkdownRenderer: MarkdownRenderer


def DefaultStyles() -> Styles:
    """The transcript's own defaults, used when a model is built bare."""
    secondary, accent = "color(8)", "color(6)"
    return Styles(
        Status=Style(color=accent, italic=True),
        UserLabel=Style(color="color(2)", bold=True),
        ToolLabel=Style(color=accent, bold=True),
        Thinking=Style(color=secondary, italic=True),
        Footer=Style(color=secondary, italic=True),
        MarkdownRenderer=_PlainMarkdownRenderer(),
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


RoleUser: Final[Role] = Role("user")
RoleAssistant: Final[Role] = Role("assistant")


@dataclasses.dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool call, exactly as the model emitted it."""

    ID: str = ""
    Name: str = ""
    Input: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Attachment:
    """One file a message carries."""

    Name: str = ""
    MIME: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Message:
    """One transcript entry. Frozen, so a rendered transcript never mutates."""

    Role: Role = RoleUser
    Content: str = ""
    ReasoningContent: str = ""
    ToolCallID: str = ""
    ToolName: str = ""
    ToolCalls: tuple[ToolCall, ...] = ()
    Interrupted: bool = False
    Attachments: tuple[Attachment, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class Intent:
    """A copy request, or the reason there is nothing to copy."""

    CopyText: str = ""
    Error: str = ""


@dataclasses.dataclass(slots=True)
class Model:
    """The managed transcript and the four expansion flags."""

    welcome: str = ""
    styles: Styles = dataclasses.field(default_factory=DefaultStyles)
    messages: list[Message] = dataclasses.field(default_factory=list[Message])
    streaming: Message | None = None
    busy: bool = False
    width: int = 80
    expandLatestTools: bool = False
    expandAllTools: bool = False
    expandLatestThink: bool = False
    expandAllThink: bool = False

    def SetWidth(self, width: int) -> None:
        """The terminal got narrower or wider."""
        self.width = max(1, width)

    def Replace(self, messages: Sequence[Message]) -> None:
        """Rebuild the transcript from conversation state."""
        self.messages = list(messages)

    def Append(self, message: Message) -> None:
        """Commit one message."""
        self.messages.append(message)

    def SetStreaming(self, message: Message | None) -> None:
        """Show, or stop showing, the message being streamed."""
        self.streaming = message

    def ClearStreaming(self) -> None:
        """Stop showing the streaming message."""
        self.streaming = None

    def SetBusy(self, busy: bool) -> None:
        """Whether a turn is running with nothing to show yet."""
        self.busy = busy

    def Update(self, key: str) -> tuple[Model, Intent | None, bool]:
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
                    blocks = ExtractCodeBlocks(message.Content)
                    if blocks:
                        return self, Intent(CopyText=blocks[-1]), True
                return self, Intent(Error="No code blocks found to copy"), True
            case _:
                return self, None, False

    def View(self) -> Text:
        """The transcript followed by whatever is streaming."""
        parts = [self.transcriptView()]
        if (stream := self.streamingView()) and stream.plain:
            parts.append(stream)
        return _join(parts, "\n\n")

    def transcriptView(self) -> Text:
        """The welcome block and every committed message, in order."""
        latestTool, latestThinking = -1, -1
        for index, message in enumerate(self.messages):
            if message.ToolCalls:
                latestTool = index
            if message.Role == RoleAssistant and message.ReasoningContent.strip():
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
                return Text("Thinking...", style=styles.Thinking)
            return Text()
        if self.streaming.Content == "" and self.streaming.ReasoningContent != "":
            return Text("Thinking...", style=styles.Thinking)
        return self.renderMessage(self.streaming, False)

    def renderCommitted(self, message: Message, toolsExpanded: bool, thinkingExpanded: bool) -> Text:
        """One committed message, with its reasoning line and expanded body."""
        content = self.renderMessage(message, toolsExpanded)
        if message.Role != RoleAssistant:
            return content
        styles = self._styles()
        thinking = Text("Thinking...", style=styles.Thinking)
        if thinkingExpanded and message.ReasoningContent.strip():
            for index, line in enumerate(message.ReasoningContent.strip().split("\n")):
                prefix = "    " if index else "  └ "
                thinking.append("\n")
                thinking.append(prefix + line, style=styles.Thinking)
        if not content.plain.strip():
            return thinking
        thinking.append("\n")
        thinking.append_text(content)
        return thinking

    def renderMessage(self, message: Message, toolsExpanded: bool) -> Text:
        """One message body: prose, tool summaries, and attachment lines."""
        styles = self._styles()
        if message.Role == RoleUser:
            rendered = Text(_PROMPT_GLYPH + " ")
            rendered.stylize(styles.UserLabel)
            rendered.append(message.Content)
            return _indent(rendered, 1)
        rendered = Text()
        if message.Role == RoleAssistant and message.Content != "":
            rendered.append_text(self._renderMarkdown(message.Content))
        if message.ToolCalls:
            if rendered.plain:
                rendered.append("\n")
            rendered.append_text(self.renderToolCalls(message.ToolCalls, toolsExpanded))
        for attachment in message.Attachments:
            rendered.append("\n")
            rendered.append(f"  attachment: {attachment.Name} ({attachment.MIME})", style=styles.Status)
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
            rendered.append("● " + summary, style=styles.ToolLabel)
            if expanded:
                for item_index, item in enumerate(group.items):
                    branch = "└" if item_index == len(group.items) - 1 else "├"
                    rendered.append("\n")
                    rendered.append(f"  {branch} {item}", style=styles.Footer)
        return rendered

    def _renderMarkdown(self, content: str) -> Text:
        """Assistant prose through the configured markdown renderer."""
        renderer = self._styles().MarkdownRenderer
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


def New(welcome: str, styles: Styles) -> Model:
    """Go's ``transcript.New``."""
    return Model(welcome=welcome, styles=styles)


def ExtractCodeBlocks(content: str) -> list[str]:
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
    args = _parseArgs(call.Input)

    def value(key: str) -> str:
        """A string argument, or the empty string when it is absent."""
        found = args.get(key)
        return found if isinstance(found, str) else ""

    def item(primary: str, fallback: str) -> list[str]:
        """One item, falling back to the tool's own name."""
        return [primary or fallback]

    match call.Name:
        case "read_file":
            return "Read", "files", item(value("path"), call.Name)
        case "write_file" | "apply_patch":
            return "Edited", "files", item(value("path"), call.Name)
        case "go_test":
            packages = _stringSlice(args.get("packages")) or ["./..."]
            return "Ran", "commands", ["go test " + " ".join(packages)]
        case "run_command" | "bash":
            return "Ran", "commands", item(value("command"), call.Name)
        case "search" | "web_search":
            return "Searched", "queries", item(value("query"), call.Name)
        case "browser_fetch":
            return "Fetched", "pages", item(value("url"), call.Name)
        case "list_files":
            return "Listed", "paths", item(value("path"), ".")
        case "format":
            files = _stringSlice(args.get("files")) or [call.Name]
            return "Formatted", "files", files
        case "git_status":
            return "Ran", "commands", ["git status --short"]
        case "git_diff":
            return "Ran", "commands", ["git diff"]
        case _:
            return "Called", "tools", [call.Name]


def _parseArgs(input_text: str) -> dict[str, Any]:
    """The tool call's JSON arguments, or an empty mapping when there are none.

    Go unmarshals into a ``map[string]any`` and ignores the error; the port keeps
    the same tolerance and normalises anything that is not an object to ``{}``.
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
    """Pad every line left by ``indent`` columns, as lipgloss PaddingLeft does."""
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
