"""The Textual inbound adapter: the only interaction surface.

This package keeps its surface in six root modules, one per concern, and this
module stands in for the package namespace so callers keep writing ``tui.new``
and ``tui.Message``.

Four things live here because they read the whole model rather than one feature:

* :func:`new` wires the features to the conversation port, including the one
  translation the root owns — the command palette's entry type into the
  composer's.
* :func:`infoBar`, :func:`welcomeString`, and :func:`footerView` compose
  cross-feature state (permission mode, attachments, approval, composer) into the
  lines around the transcript.
* :func:`applyOutcome` is the routing table: a command reports its whole effect,
  and the root applies the parts it owns and hands the cross-feature parts to the
  feature that owns them.

``tui`` may not import ``super_agent.runtime`` (R1) and a feature may not import a
sibling feature or this package (R6, R8). Both are enforced by
``tests/architecture/test_dependencies.py``.
"""

from __future__ import annotations

import dataclasses
import os

from rich.text import Text

from super_agent.tui import approval as approval_feature, attachments, commands, composer, runtime, transcript
from super_agent.tui.actions import (
    ClipboardDone as ClipboardDone,
    extract_code_blocks as extract_code_blocks,
    finishCopy as finishCopy,
)
from super_agent.tui.app import (
    App as App,
    ClipboardWriter as ClipboardWriter,
    ConversationNotificationMsg as ConversationNotificationMsg,
    Msg as Msg,
    Option as Option,
    OutputPrinter as OutputPrinter,
    StartupInfo as StartupInfo,
    SubmitDone as SubmitDone,
    compactStatus as compactStatus,
    composerCommands as composerCommands,
    displayCWD as displayCWD,
    printCommand as printCommand,
    scrollbackPrinter as scrollbackPrinter,
    with_clipboard_writer as with_clipboard_writer,
    with_output_printer as with_output_printer,
)
from super_agent.tui.application import Application as Application
from super_agent.tui.approval import (
    APPROVE_ALWAYS as APPROVE_ALWAYS,
    APPROVE_ONCE as APPROVE_ONCE,
    DENY as DENY_APPROVAL,
    Decision as ApprovalDecision,
)
from super_agent.tui.commands import (
    AgentSummary as AgentSummary,
    MCPServerSummary as MCPServerSummary,
    SessionSummary as SessionSummary,
)
from super_agent.tui.conversation import (
    NOTIFICATION_KINDS as NOTIFICATION_KINDS,
    ROLE_ASSISTANT as ROLE_ASSISTANT,
    AgentStatus as AgentStatus,
    AgentStatusChanged as AgentStatusChanged,
    AttachmentSummary as AttachmentSummary,
    Cancellation as Cancellation,
    Channel as Channel,
    ContextUsage as ContextUsage,
    Conversation as Conversation,
    ConversationError as ConversationError,
    ConversationNotification as ConversationNotification,
    ConversationView as ConversationView,
    Message as Message,
    MessageAppended as MessageAppended,
    MessageAttachment as MessageAttachment,
    PermissionRequest as PermissionRequest,
    Role as Role,
    SnapshotPort as SnapshotPort,
    StreamChunkReceived as StreamChunkReceived,
    ToolApprovalCleared as ToolApprovalCleared,
    ToolApprovalRequested as ToolApprovalRequested,
    ToolCall as ToolCall,
    TurnPort as TurnPort,
    UsageReported as UsageReported,
)
from super_agent.tui.runtime import (
    CLEAR_SCREEN as CLEAR_SCREEN,
    QUIT as QUIT,
    ClearScreenMsg as ClearScreenMsg,
    Command as Command,
    KeyDecoder as KeyDecoder,
    KeyMsg as KeyMsg,
    Listener as Listener,
    Program as Program,
    QuitMsg as QuitMsg,
    Scrollback as Scrollback,
    WindowSizeMsg as WindowSizeMsg,
    batch as batch,
)
from super_agent.tui.styles import (
    MarkdownRenderer as MarkdownRenderer,
    Styles as Styles,
    default_styles as default_styles,
)
from super_agent.tui.transcript import MarkdownRenderer as TranscriptMarkdownRenderer, Styles as TranscriptStyles
from super_agent.tui.update import (
    finishSubmit as finishSubmit,
    pendingAttachments as pendingAttachments,
    queueInput as queueInput,
    resize as resize,
    steerInput as steerInput,
    submitPrompt as submitPrompt,
    submitText as submitText,
    update as update,
    updateConversationNotification as updateConversationNotification,
    updateKey as updateKey,
    waitForNotification as waitForNotification,
)
from super_agent.tui.view import (
    clampLines as clampLines,
    fitDynamicArea as fitDynamicArea,
    helpView as helpView,
    view as view,
)

__all__ = [
    "APPROVE_ALWAYS",
    "APPROVE_ONCE",
    "CLEAR_SCREEN",
    "DENY_APPROVAL",
    "NOTIFICATION_KINDS",
    "QUIT",
    "ROLE_ASSISTANT",
    "AgentStatus",
    "AgentStatusChanged",
    "AgentSummary",
    "App",
    "Application",
    "ApprovalDecision",
    "AttachmentSummary",
    "Cancellation",
    "Channel",
    "ClearScreenMsg",
    "ClipboardDone",
    "ClipboardWriter",
    "Command",
    "ContextUsage",
    "Conversation",
    "ConversationError",
    "ConversationNotification",
    "ConversationNotificationMsg",
    "ConversationView",
    "KeyDecoder",
    "KeyMsg",
    "Listener",
    "MCPServerSummary",
    "MarkdownRenderer",
    "Message",
    "MessageAppended",
    "MessageAttachment",
    "Msg",
    "Option",
    "OutputPrinter",
    "PermissionRequest",
    "Program",
    "QuitMsg",
    "Role",
    "Scrollback",
    "SessionSummary",
    "SnapshotPort",
    "StartupInfo",
    "StreamChunkReceived",
    "Styles",
    "SubmitDone",
    "ToolApprovalCleared",
    "ToolApprovalRequested",
    "ToolCall",
    "TranscriptMarkdownRenderer",
    "TranscriptStyles",
    "TurnPort",
    "UsageReported",
    "WindowSizeMsg",
    "applyOutcome",
    "batch",
    "clampLines",
    "compactStatus",
    "composerCommands",
    "default_styles",
    "displayCWD",
    "extract_code_blocks",
    "finishCopy",
    "finishSubmit",
    "fitDynamicArea",
    "footerView",
    "helpView",
    "infoBar",
    "new",
    "pendingAttachments",
    "printCommand",
    "queueInput",
    "refreshSnapshot",
    "resize",
    "scrollbackPrinter",
    "steerInput",
    "submitPrompt",
    "submitText",
    "update",
    "updateConversationNotification",
    "updateKey",
    "view",
    "waitForNotification",
    "welcomeString",
    "with_clipboard_writer",
    "with_output_printer",
]


def new(session: Conversation, info: StartupInfo, *options: Option) -> App:
    """Build the model and wire every feature to the port."""
    styles = default_styles()
    command_model = commands.new(
        commands.Config(cwd=info.cwd, instruction_paths=info.instruction_paths, no_tools=info.no_tools),
        commands.Ports(
            sessions=session,
            permissions=session,
            mcp=session,
            agents=session,
            memory=session,
            workspace=session,
            extensions=session,
        ),
    )
    app = App(
        snapshot=session,
        turnPort=session,
        commands=command_model,
        composer=composer.new(
            composerCommands(command_model.palette()),
            styles=composer.Styles(
                prompt=styles.accent_bold,
                accent=styles.accent,
                selected=styles.accent_bold,
                dim=styles.secondary,
            ),
        ),
        approval=approval_feature.Model(
            styles=approval_feature.Styles(
                banner=styles.banner,
                dim=styles.secondary,
                selected=styles.accent_bold,
                accent=styles.accent,
            )
        ),
        attachments=attachments.new(session, styles=attachments.Styles(accent=styles.accent)),
        transcript=transcript.Model(welcome=""),
        styles=styles,
        info=info,
    )
    app.transcript = transcript.new(
        welcomeString(app),
        TranscriptStyles(
            default=styles.default,
            secondary=styles.secondary,
            accent=styles.accent,
            accent_bold=styles.accent_bold,
            identity=styles.identity,
            success=styles.success,
            error=styles.error,
            markdown_renderer=styles.markdown_renderer,
        ),
    )
    for option in options:
        option(app)
    return app


def infoBar(app: App) -> Text:
    """The bottom status line: permission mode, model, and whether tools are on."""
    tools = "tools off" if app.info.no_tools else "tools on"
    parts = [app.info.permission_mode or "ask", app.info.model_name, tools]
    rendered = Text()
    for index, part in enumerate(parts):
        if index:
            rendered.append(" · ", style=app.styles.secondary)
        rendered.append(part, style=app.styles.secondary)
    return clampLines(app.width, rendered)


def welcomeString(app: App) -> str:
    """The compact welcome block: product, model, working directory, instructions."""
    parts = [app.info.model_name]
    if location := displayCWD(app.info.cwd):
        parts.append(location)
    if app.info.instruction_paths:
        parts.append(os.path.basename(app.info.instruction_paths[-1]))
    return "Super Agent\n" + " · ".join(parts)


def footerView(app: App) -> Text:
    """The error or status line, then the attachment, approval, and input rows."""
    rendered = Text()
    if app.err:
        rendered.append(" !! error: " + app.err, style=app.styles.error)
    elif app.status:
        rendered.append(" " + compactStatus(app.status, 3), style=app.styles.accent)
    for part in (app.attachments.view(), app.approval.view(app.info.cwd)):
        if part.plain:
            rendered.append("\n")
            rendered.append_text(part)
    rendered.append("\n")
    rendered.append_text(app.composer.view())
    rendered.append("\n")
    rendered.append_text(infoBar(app))
    return clampLines(app.width, rendered)


def refreshSnapshot(app: App) -> None:
    """Re-read conversation state after a command replaced or shrank the transcript."""
    snapshot = app.snapshot.snapshot()
    app.agentStatus = snapshot.agent_status
    app.transcript.replace(snapshot.messages)
    app.transcript.set_busy(snapshot.agent_status.busy)
    app.approval.clear()
    call, request = snapshot.pending_tool, snapshot.pending_permission
    if call is not None and request is not None:
        app.approval.open(
            approval_feature.Request(
                tool_name=call.name,
                input=call.input,
                command_class=request.command_class,
                cwd=request.cwd,
                touched_paths=request.touched_paths,
                reason=request.reason,
                batch_index=snapshot.pending_tool_batch_index,
                batch_total=snapshot.pending_tool_batch_total,
            )
        )
    app.transcript.set_streaming(snapshot.streaming_message)


async def applyOutcome(
    app: App,
    outcome: commands.Outcome | None,
    command: tuple[runtime.Command[Msg], ...],
) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Carry out a command feature's requests.

    It only routes and applies: the feature owns the semantics, the root owns the
    effects and the wiring to the features a command reaches across. The routes
    are exclusive and fixed in order — an outcome that both attaches and prompts
    only attaches.
    """
    if outcome is None:
        return app, command
    if outcome.err:
        app.err = outcome.err
    else:
        app.err = ""
        if outcome.status:
            app.status = outcome.status
    if outcome.status_bar is not None:
        if outcome.status_bar.model_name:
            app.info = dataclasses.replace(app.info, model_name=outcome.status_bar.model_name)
        app.info = dataclasses.replace(app.info, permission_mode=outcome.status_bar.permission_mode)
    if outcome.refresh_snapshot:
        refreshSnapshot(app)
    if outcome.quit:
        return app, (runtime.QUIT,)
    if outcome.show_help:
        app.showHelp = True
    if outcome.attach_path:
        return app, (app.attachments.attach(outcome.attach_path),)
    if outcome.prompt:
        return submitPrompt(app, outcome.prompt)
    if outcome.output:
        return app, runtime.batch(printCommand(app, outcome.output))
    return app, command
