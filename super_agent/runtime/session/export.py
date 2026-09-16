"""Writing a session out as markdown, JSON, or HTML."""

from __future__ import annotations

import html
import posixpath
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from super_agent import jsonutil
from super_agent.runtime.machine import Message
from super_agent.runtime.session.repository import AuditEvent, Metadata
from super_agent.timeutil import FormatRFC3339

if TYPE_CHECKING:
    from super_agent.runtime.engine import EngineView
    from super_agent.runtime.session.repository import Repository, Workspace


@runtime_checkable
class ExportWriter(Protocol):
    """A workspace that can write an export file."""

    def WriteExport(self, path: str, content: bytes) -> str: ...


class ExportMixin:
    """The export use case."""

    if TYPE_CHECKING:
        repository: Repository | None
        workspace: Workspace | None

        def Metadata(self) -> Metadata: ...
        def Snapshot(self) -> EngineView: ...

    def Export(self, format: str) -> str:
        """Write the transcript and its audit trail, returning the written path."""
        if not isinstance(self.workspace, ExportWriter):
            raise RuntimeError("session export is unavailable")
        normalised = format.strip().lower()
        if normalised == "md":
            normalised = "markdown"
        meta = self.Metadata()
        messages = list(self.Snapshot().Messages)
        events: list[AuditEvent] = []
        if self.repository is not None:
            events = self.repository.LoadAuditEvents(meta.ID)

        if normalised == "markdown":
            content = markdownExport(meta, messages, events).encode()
            extension = ".md"
        elif normalised == "json":
            encoded = jsonutil.dumps(
                {
                    "metadata": meta,
                    "messages": list(messages),
                    "events": list(events),
                },
                indent=2,
            )
            content = (encoded + "\n").encode()
            extension = ".json"
        elif normalised == "html":
            content = htmlExport(meta, messages, events).encode()
            extension = ".html"
        else:
            raise ValueError("export format must be markdown, json, or html")

        target = posixpath.join(".super-agent", "exports", str(meta.ID) + extension)
        return self.workspace.WriteExport(target, content)


def markdownExport(meta: Metadata, messages: list[Message], events: list[AuditEvent]) -> str:
    output = [
        f"# {meta.Title}\n\n",
        f"- Session: `{meta.ID}`\n",
        f"- Model: `{meta.Provider}/{meta.Model}`\n",
        f"- Working directory: `{meta.CWD}`\n\n",
    ]
    for message in messages:
        output.append(f"## {_title(str(message.Role))}\n\n{message.Content}\n\n")
    output.append(_markdownEvents(events))
    return "".join(output)


def _markdownEvents(events: list[AuditEvent]) -> str:
    if not events:
        return ""
    lines = ["## Audit events\n\n"]
    for event in events:
        stamp = FormatRFC3339(event.Time) if event.Time is not None else ""
        line = f"- `{event.Type}` {stamp}"
        if event.ToolCall is not None:
            line += f" tool=`{event.ToolCall.Name}`"
        if event.Decision != "":
            line += f" decision=`{event.Decision}`"
        if event.Error != "":
            line += f" error={event.Error}"
        if event.Result != "":
            line += f" result={event.Result}"
        lines.append(line + "\n")
    return "".join(lines)


def htmlExport(meta: Metadata, messages: list[Message], events: list[AuditEvent]) -> str:
    body: list[str] = []
    for message in messages:
        body.append(
            f"<section><h2>{html.escape(str(message.Role))}</h2><pre>{html.escape(message.Content)}</pre></section>"
        )
    if events:
        body.append("<section><h2>Audit events</h2><ul>")
        for event in events:
            detail = event.Decision + event.Error + event.Result
            if event.ToolCall is not None:
                detail = event.ToolCall.Name + " " + detail
            body.append(f"<li><code>{html.escape(event.Type)}</code> {html.escape(detail.strip())}</li>")
        body.append("</ul></section>")
    return (
        '<!doctype html><meta charset="utf-8"><title>'
        + html.escape(meta.Title)
        + "</title><style>body{max-width:900px;margin:40px auto;font:16px system-ui}"
        "pre{white-space:pre-wrap;background:#f5f5f5;padding:16px;border-radius:8px}</style><h1>"
        + html.escape(meta.Title)
        + "</h1>"
        + "".join(body)
    )


def _title(text: str) -> str:
    """Capitalise each word, for the values used here."""
    return " ".join(word[:1].upper() + word[1:] for word in text.split(" "))
