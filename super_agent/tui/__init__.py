"""The Rich inbound adapter: the only interaction surface.

Ported from the Go ``tui`` package. Go keeps this package in six root files; the
port keeps the same split with one module each, and this module stands in for the
package namespace so Go callers keep writing ``tui.New`` and ``tui.Message``.

Four things live here because they read the whole model rather than one feature:

* :func:`New` wires the features to the conversation port, including the one
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
    ExtractCodeBlocks as ExtractCodeBlocks,
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
    WithClipboardWriter as WithClipboardWriter,
    WithOutputPrinter as WithOutputPrinter,
    compactStatus as compactStatus,
    composerCommands as composerCommands,
    displayCWD as displayCWD,
    printCommand as printCommand,
    scrollbackPrinter as scrollbackPrinter,
)
from super_agent.tui.approval import (
    ApproveAlways as ApproveAlways,
    ApproveOnce as ApproveOnce,
    Decision as ApprovalDecision,
    Deny as DenyApproval,
)
from super_agent.tui.commands import (
    AgentSummary as AgentSummary,
    MCPServerSummary as MCPServerSummary,
    SessionSummary as SessionSummary,
)
from super_agent.tui.conversation import (
    NOTIFICATION_KINDS as NOTIFICATION_KINDS,
    AgentStatus as AgentStatus,
    AgentStatusChanged as AgentStatusChanged,
    AttachmentSummary as AttachmentSummary,
    Cancellation as Cancellation,
    Channel as Channel,
    Conversation as Conversation,
    ConversationError as ConversationError,
    ConversationNotification as ConversationNotification,
    ConversationView as ConversationView,
    Message as Message,
    MessageAppended as MessageAppended,
    MessageAttachment as MessageAttachment,
    PermissionRequest as PermissionRequest,
    Role as Role,
    RoleAssistant as RoleAssistant,
    SnapshotPort as SnapshotPort,
    StreamChunkReceived as StreamChunkReceived,
    ToolApprovalCleared as ToolApprovalCleared,
    ToolApprovalRequested as ToolApprovalRequested,
    ToolCall as ToolCall,
    TurnPort as TurnPort,
)
from super_agent.tui.runtime import (
    ClearScreen as ClearScreen,
    ClearScreenMsg as ClearScreenMsg,
    Command as Command,
    KeyDecoder as KeyDecoder,
    KeyMsg as KeyMsg,
    Listener as Listener,
    Program as Program,
    Quit as Quit,
    QuitMsg as QuitMsg,
    Scrollback as Scrollback,
    WindowSizeMsg as WindowSizeMsg,
    batch as batch,
)
from super_agent.tui.styles import (
    DefaultStyles as DefaultStyles,
    MarkdownRenderer as MarkdownRenderer,
    Styles as Styles,
)
from super_agent.tui.transcript import MarkdownRenderer as TranscriptMarkdownRenderer, Styles as TranscriptStyles
from super_agent.tui.update import (
    Update as Update,
    finishSubmit as finishSubmit,
    pendingAttachments as pendingAttachments,
    queueInput as queueInput,
    resize as resize,
    steerInput as steerInput,
    submitPrompt as submitPrompt,
    submitText as submitText,
    updateConversationNotification as updateConversationNotification,
    updateKey as updateKey,
    waitForNotification as waitForNotification,
)
from super_agent.tui.view import (
    View as View,
    clampLines as clampLines,
    fitDynamicArea as fitDynamicArea,
    helpView as helpView,
)

__all__ = [
    "NOTIFICATION_KINDS",
    "AgentStatus",
    "AgentStatusChanged",
    "AgentSummary",
    "App",
    "ApprovalDecision",
    "ApproveAlways",
    "ApproveOnce",
    "AttachmentSummary",
    "Cancellation",
    "Channel",
    "ClearScreen",
    "ClearScreenMsg",
    "ClipboardDone",
    "ClipboardWriter",
    "Command",
    "Conversation",
    "ConversationError",
    "ConversationNotification",
    "ConversationNotificationMsg",
    "ConversationView",
    "DefaultStyles",
    "DenyApproval",
    "ExtractCodeBlocks",
    "KeyDecoder",
    "KeyMsg",
    "Listener",
    "MCPServerSummary",
    "MarkdownRenderer",
    "Message",
    "MessageAppended",
    "MessageAttachment",
    "Msg",
    "New",
    "Option",
    "OutputPrinter",
    "PermissionRequest",
    "Program",
    "Quit",
    "QuitMsg",
    "Role",
    "RoleAssistant",
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
    "Update",
    "View",
    "WindowSizeMsg",
    "WithClipboardWriter",
    "WithOutputPrinter",
    "applyOutcome",
    "batch",
    "clampLines",
    "compactStatus",
    "composerCommands",
    "displayCWD",
    "finishCopy",
    "finishSubmit",
    "fitDynamicArea",
    "footerView",
    "helpView",
    "infoBar",
    "pendingAttachments",
    "printCommand",
    "queueInput",
    "refreshSnapshot",
    "resize",
    "scrollbackPrinter",
    "steerInput",
    "submitPrompt",
    "submitText",
    "updateConversationNotification",
    "updateKey",
    "waitForNotification",
    "welcomeString",
]


def New(session: Conversation, info: StartupInfo, *options: Option) -> App:
    """Go's ``tui.New``: build the model and wire every feature to the port."""
    styles = DefaultStyles()
    command_model = commands.New(
        commands.Config(CWD=info.CWD, InstructionPaths=info.InstructionPaths, NoTools=info.NoTools),
        commands.Ports(
            Sessions=session,
            Permissions=session,
            MCP=session,
            Agents=session,
            Memory=session,
            Workspace=session,
            Extensions=session,
        ),
    )
    app = App(
        snapshot=session,
        turnPort=session,
        commands=command_model,
        composer=composer.New(composerCommands(command_model.Palette())),
        approval=approval_feature.Model(),
        attachments=attachments.New(session),
        transcript=transcript.Model(welcome=""),
        styles=styles,
        info=info,
    )
    app.transcript = transcript.New(
        welcomeString(app),
        TranscriptStyles(
            Status=styles.Status,
            UserLabel=styles.UserLabel,
            ToolLabel=styles.ToolLabel,
            Thinking=styles.Thinking,
            Footer=styles.Footer,
            MarkdownRenderer=styles.MarkdownRenderer,
        ),
    )
    for option in options:
        option(app)
    return app


def infoBar(app: App) -> Text:
    """The bottom status line: permission mode, model, and whether tools are on."""
    tools = "tools off" if app.info.NoTools else "tools on"
    parts = [app.info.PermissionMode or "ask", app.info.ModelName, tools]
    rendered = Text()
    for index, part in enumerate(parts):
        if index:
            rendered.append(" · ", style=app.styles.Footer)
        rendered.append(part, style=app.styles.Footer)
    return clampLines(app.width, rendered)


def welcomeString(app: App) -> str:
    """The compact welcome block: product, model, working directory, instructions."""
    parts = [app.info.ModelName]
    if location := displayCWD(app.info.CWD):
        parts.append(location)
    if app.info.InstructionPaths:
        parts.append(os.path.basename(app.info.InstructionPaths[-1]))
    return "Super Agent\n" + " · ".join(parts)


def footerView(app: App) -> Text:
    """The error or status line, then the attachment, approval, and input rows."""
    rendered = Text()
    if app.err:
        rendered.append(" !! error: " + app.err, style=app.styles.Error)
    elif app.status:
        rendered.append(" " + compactStatus(app.status, 3), style=app.styles.Status)
    for view in (app.attachments.View(), app.approval.View(app.info.CWD)):
        if view.plain:
            rendered.append("\n")
            rendered.append_text(view)
    rendered.append("\n")
    rendered.append_text(app.composer.View())
    rendered.append("\n")
    rendered.append_text(infoBar(app))
    return clampLines(app.width, rendered)


def refreshSnapshot(app: App) -> None:
    """Re-read conversation state after a command replaced or shrank the transcript."""
    snapshot = app.snapshot.Snapshot()
    app.agentStatus = snapshot.AgentStatus
    app.transcript.Replace(snapshot.Messages)
    app.transcript.SetBusy(snapshot.AgentStatus.Busy)
    app.approval.Clear()
    call, request = snapshot.PendingTool, snapshot.PendingPermission
    if call is not None and request is not None:
        app.approval.Open(
            approval_feature.Request(
                ToolName=call.Name,
                Input=call.Input,
                CommandClass=request.CommandClass,
                CWD=request.CWD,
                TouchedPaths=request.TouchedPaths,
                Reason=request.Reason,
                BatchIndex=snapshot.PendingToolBatchIndex,
                BatchTotal=snapshot.PendingToolBatchTotal,
            )
        )
    app.transcript.SetStreaming(snapshot.StreamingMessage)


async def applyOutcome(
    app: App,
    outcome: commands.Outcome | None,
    command: tuple[runtime.Command[Msg], ...],
) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Carry out a command feature's requests.

    It only routes and applies: the feature owns the semantics, the root owns the
    effects and the wiring to the features a command reaches across. The routes
    are exclusive and in Go's order — an outcome that both attaches and prompts
    only attaches.
    """
    if outcome is None:
        return app, command
    if outcome.Err:
        app.err = outcome.Err
    else:
        app.err = ""
        if outcome.Status:
            app.status = outcome.Status
    if outcome.StatusBar is not None:
        if outcome.StatusBar.ModelName:
            app.info = dataclasses.replace(app.info, ModelName=outcome.StatusBar.ModelName)
        app.info = dataclasses.replace(app.info, PermissionMode=outcome.StatusBar.PermissionMode)
    if outcome.RefreshSnapshot:
        refreshSnapshot(app)
    if outcome.Quit:
        return app, (runtime.Quit,)
    if outcome.ShowHelp:
        app.showHelp = True
    if outcome.AttachPath:
        return app, (app.attachments.Attach(outcome.AttachPath),)
    if outcome.Prompt:
        return submitPrompt(app, outcome.Prompt)
    if outcome.Output:
        return app, runtime.batch(printCommand(app, outcome.Output))
    return app, command
