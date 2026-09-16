"""Writing a session out as markdown, JSON, or HTML."""

from __future__ import annotations

import html
import posixpath
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from super_agent import jsonutil
from super_agent.runtime.machine import Message
from super_agent.runtime.session.repository import AuditEvent, Metadata
from super_agent.timeutil import format_rfc3339

if TYPE_CHECKING:
    from super_agent.runtime.engine import EngineView
    from super_agent.runtime.session.repository import Repository, Workspace


@runtime_checkable
class ExportWriter(Protocol):
    """A workspace that can write an export file."""

    def write_export(self, path: str, content: bytes) -> str: ...


class ExportMixin:
    """The export use case."""

    if TYPE_CHECKING:
        repository: Repository | None
        workspace: Workspace | None

        def metadata(self) -> Metadata: ...
        def snapshot(self) -> EngineView: ...

    def export(self, format: str) -> str:
        """Write the transcript and its audit trail, returning the written path."""
        if not isinstance(self.workspace, ExportWriter):
            raise RuntimeError("session export is unavailable")
        normalised = format.strip().lower()
        if normalised == "md":
            normalised = "markdown"
        meta = self.metadata()
        messages = list(self.snapshot().messages)
        events: list[AuditEvent] = []
        if self.repository is not None:
            events = self.repository.load_audit_events(meta.id)

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

        target = posixpath.join(".super-agent", "exports", str(meta.id) + extension)
        return self.workspace.write_export(target, content)


def markdownExport(meta: Metadata, messages: list[Message], events: list[AuditEvent]) -> str:
    output = [
        f"# {meta.title}\n\n",
        f"- Session: `{meta.id}`\n",
        f"- Model: `{meta.provider}/{meta.model}`\n",
        f"- Working directory: `{meta.cwd}`\n\n",
    ]
    for message in messages:
        output.append(f"## {_title(str(message.role))}\n\n{message.content}\n\n")
    output.append(_markdownEvents(events))
    return "".join(output)


def _markdownEvents(events: list[AuditEvent]) -> str:
    if not events:
        return ""
    lines = ["## Audit events\n\n"]
    for event in events:
        stamp = format_rfc3339(event.time) if event.time is not None else ""
        line = f"- `{event.type}` {stamp}"
        if event.tool_call is not None:
            line += f" tool=`{event.tool_call.name}`"
        if event.decision != "":
            line += f" decision=`{event.decision}`"
        if event.error != "":
            line += f" error={event.error}"
        if event.result != "":
            line += f" result={event.result}"
        lines.append(line + "\n")
    return "".join(lines)


def htmlExport(meta: Metadata, messages: list[Message], events: list[AuditEvent]) -> str:
    body: list[str] = []
    for message in messages:
        body.append(
            f"<section><h2>{html.escape(str(message.role))}</h2><pre>{html.escape(message.content)}</pre></section>"
        )
    if events:
        body.append("<section><h2>Audit events</h2><ul>")
        for event in events:
            detail = event.decision + event.error + event.result
            if event.tool_call is not None:
                detail = event.tool_call.name + " " + detail
            body.append(f"<li><code>{html.escape(event.type)}</code> {html.escape(detail.strip())}</li>")
        body.append("</ul></section>")
    return (
        '<!doctype html><meta charset="utf-8"><title>'
        + html.escape(meta.title)
        + "</title><style>body{max-width:900px;margin:40px auto;font:16px system-ui}"
        "pre{white-space:pre-wrap;background:#f5f5f5;padding:16px;border-radius:8px}</style><h1>"
        + html.escape(meta.title)
        + "</h1>"
        + "".join(body)
    )


def _title(text: str) -> str:
    """Capitalise each word, for the values used here."""
    return " ".join(word[:1].upper() + word[1:] for word in text.split(" "))
