"""The app's update: one message in, the model and its commands out.

Message routing order and key dispatch order are both load-bearing — they decide
which feature sees a key first — so both are fixed:

* messages: window size, then feature results, then a key press, then a
  conversation notification, then a finished turn;
* keys: help overlay, then approval, then the root's own keys, then the
  transcript, then the composer.

One shape difference: ``Update`` is a coroutine. The key branch reaches the
command feature, which awaits its ports, so the caller awaits the update.
"""

from __future__ import annotations

from super_agent.errors import Cancelled, errors_is
from super_agent.tui import actions, attachments, commands, runtime
from super_agent.tui.app import App, ConversationNotificationMsg, Msg, SubmitDone
from super_agent.tui.approval import Request
from super_agent.tui.composer import IntentKind
from super_agent.tui.conversation import (
    AgentStatus,
    AgentStatusChanged,
    ApprovalDecision,
    Cancellation,
    Channel,
    ConversationError,
    ConversationNotification,
    MessageAppended,
    StreamChunkReceived,
    ToolApprovalCleared,
    ToolApprovalRequested,
)
from super_agent.tui.transcript import RoleAssistant

__all__ = [
    "Update",
    "finishSubmit",
    "pendingAttachments",
    "queueInput",
    "resize",
    "runTurn",
    "steerInput",
    "submitPrompt",
    "submitText",
    "updateAttachments",
    "updateCommands",
    "updateConversationNotification",
    "updateKey",
    "waitForNotification",
]

#: The keys that reach the root even while the approval menu is open.
_CANCEL_KEYS = ("ctrl+c", "esc")


async def Update(app: App, message: Msg) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Route one message in the fixed dispatch order the app relies on."""
    if isinstance(message, runtime.WindowSizeMsg):
        return resize(app, message)
    if isinstance(message, actions.ClipboardDone):
        return actions.finishCopy(app, message), ()
    if isinstance(message, (attachments.Loaded, attachments.Attached)):
        return updateAttachments(app, message)
    if isinstance(message, (commands.CompactDone, commands.MCPDone)):
        return await updateCommands(app, message)
    if isinstance(message, runtime.KeyMsg):
        return await updateKey(app, message.Key)
    if isinstance(message, ConversationNotificationMsg):
        if message.Turn != app.turn:
            # A stale listener on a replaced turn channel delivered a leftover
            # notification after a new turn started. Drop it and keep listening
            # on the current channel.
            return app, (waitForNotification(app.notifications, app.turn),)
        return updateConversationNotification(app, message.Notification)
    if isinstance(message, SubmitDone):
        return finishSubmit(app, message.Err)
    # Everything else would belong to the composer, where only the textarea's
    # own messages live. This composer has none, so there is nothing to do.
    return app, ()


def resize(app: App, message: runtime.WindowSizeMsg) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Take the terminal's new dimensions and re-derive every budget."""
    app.width, app.height = max(1, message.Width), max(1, message.Height)
    app.ready = True
    app.composer.SetWidth(app.width)
    app.composer.SetCompactPalette(app.height < 18)
    app.transcript.SetWidth(app.width)
    return app, ()


def updateAttachments(
    app: App, message: attachments.Loaded | attachments.Attached
) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Apply an attachment result: a load is silent, an attach reports."""
    model, outcome = app.attachments.Update(message)
    app.attachments = model
    if outcome is None:
        return app, ()
    if outcome.Err is not None:
        app.err = f"Attach failed: {outcome.Err}"
        return app, ()
    app.err = ""
    attached = outcome.Attached
    if attached is not None:
        app.status = f"Attached {attached.Name} ({attached.MIME})"
    return app, ()


async def updateCommands(
    app: App, message: commands.CompactDone | commands.MCPDone
) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Apply a background command's result through the root's routing table."""
    model, outcome = app.commands.Update(message)
    app.commands = model
    return await _applyOutcome(app, outcome, ())


async def updateKey(app: App, key: str) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Dispatch one key press, from the most global owner to the most focused."""
    if app.showHelp:
        if key in ("?", "esc"):
            app.showHelp = False
        return app, ()
    if app.approval.Active() and key not in _CANCEL_KEYS:
        _, decision, submitted = app.approval.Update(key)
        if submitted:
            app.transcript.ClearStreaming()
            app.approvals.Put(decision)
        return app, ()
    match key:
        case "ctrl+c":
            if app.approval.Active() or app.cancellation is not None:
                return await actions.cancelRun(app, True), ()
            return app, (runtime.Quit,)
        case "esc":
            if app.approval.Active() or app.cancellation is not None:
                return await actions.cancelRun(app, True), ()
        case "ctrl+l":
            app.err = ""
            app.status = ""
            return app, (runtime.ClearScreen,)
        case "?":
            if app.composer.Value() == "":
                app.showHelp = True
                app.status = ""
                return app, ()
        case "pgup" | "pgdown":
            # Page keys belong to the terminal's own scrollback.
            return app, ()
        case _:
            # Every other key belongs to the transcript, and then the composer.
            # ``ctrl+y`` is deliberately one of them: the copy binding is the
            # transcript's, not the root's.
            pass
    model, intent, consumed = app.transcript.Update(key)
    app.transcript = model
    if consumed:
        if intent is None:
            return app, ()
        if intent.Error:
            app.err, app.status = intent.Error, ""
            return app, ()
        app.err = ""
        return app, runtime.batch(actions.copyCommand(app, intent.CopyText))
    app.composer.SetTurnRunning(app.cancellation is not None)
    composer, composerIntent = app.composer.Update(key)
    app.composer = composer
    if composerIntent is None:
        return app, ()
    match composerIntent.Kind:
        case IntentKind.Submit:
            return await submitText(app, composerIntent.Text)
        case IntentKind.Queue:
            return queueInput(app, composerIntent.Text)
        case IntentKind.Steer:
            return await steerInput(app, composerIntent.Text)
        case IntentKind.Clear:
            app.status = "Input cleared"
        case _:
            # ``NoIntent`` never reaches here: an absent intent is tested above.
            pass
    return app, ()


def updateConversationNotification(
    app: App, notification: ConversationNotification
) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Apply one notification, then re-arm the listener for the next one."""
    if isinstance(notification, AgentStatusChanged):
        app.agentStatus = notification.Status
        app.transcript.SetBusy(notification.Status.Busy)
        if not app.needsInput():
            app.approval.Clear()
    elif isinstance(notification, ToolApprovalRequested):
        request = notification.Request
        app.approval.Open(
            Request(
                ToolName=notification.ToolCall.Name,
                Input=notification.ToolCall.Input,
                CommandClass=request.CommandClass,
                CWD=request.CWD,
                TouchedPaths=request.TouchedPaths,
                Reason=request.Reason,
                BatchIndex=notification.BatchIndex,
                BatchTotal=notification.BatchTotal,
            )
        )
    elif isinstance(notification, ToolApprovalCleared):
        app.approval.Clear()
    elif isinstance(notification, MessageAppended):
        app.transcript.Append(notification.Message)
        if notification.Message.Role == RoleAssistant:
            app.transcript.ClearStreaming()
    elif isinstance(notification, ConversationError):
        if notification.Err is not None and not errors_is(notification.Err, Cancelled):
            app.err = str(notification.Err)
    elif isinstance(notification, StreamChunkReceived):
        app.transcript.SetStreaming(notification.Message)
    return app, (waitForNotification(app.notifications, app.turn),)


async def submitText(app: App, text: str) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Route submitted input to the feature that owns its meaning.

    Slash commands belong to the command feature; everything else starts a turn.
    """
    if app.commands.Compacting() or app.commands.ManagingMCP():
        app.status = "Background operation in progress…"
        return app, ()
    if not commands.IsCommand(text):
        return submitPrompt(app, text)
    app.composer.ClearInput()
    model, outcome, command = await app.commands.Handle(
        commands.Input(Text=text, Attachments=pendingAttachments(app.attachments.Items()))
    )
    app.commands = model
    return await _applyOutcome(app, outcome, runtime.batch(command))


def queueInput(app: App, text: str) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Queue a follow-up for after the running turn."""
    if app.commands.Compacting():
        app.status = "Compacting conversation…"
        return app, ()
    if commands.IsCommand(text):
        app.err = "Slash commands are unavailable while a turn is running"
        return app, ()
    app.composer.Enqueue(text)
    app.composer.ClearInput()
    app.status = "Message queued"
    return app, ()


async def steerInput(app: App, text: str) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Cancel the running turn and run ``text`` when it finishes.

    The queue survives, because a steering cancellation means the user is
    mid-thought rather than abandoning the work.
    """
    if commands.IsCommand(text):
        app.err = "Slash commands are unavailable while a turn is running"
        return app, ()
    app.composer.Prepend(text)
    app.composer.ClearInput()
    await actions.cancelRun(app, False)
    app.status = "Steering current turn"
    return app, ()


def pendingAttachments(items: tuple[attachments.Item, ...]) -> tuple[commands.Attachment, ...]:
    """The queue, in the vocabulary the command feature owns."""
    return tuple(commands.Attachment(Name=item.Name, MIME=item.MIME) for item in items)


def finishSubmit(app: App, err: BaseException | None) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Close out a turn, and start the next queued one when there is one."""
    app.cancellation = None
    app.composer.SetTurnRunning(False)
    if err is not None and not errors_is(err, Cancelled):
        app.err = str(err)
    next_text, queued = app.composer.NextQueued()
    if queued:
        return submitPrompt(app, next_text)
    return app, ()


def submitPrompt(app: App, text: str) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Start a turn: fresh channels, a fresh cancellation, and a new turn number."""
    app.err = ""
    app.status = ""
    app.transcript.ClearStreaming()
    app.agentStatus = AgentStatus(Label="Submitting", Busy=True)
    app.transcript.SetBusy(True)
    app.approval.Clear()
    app.composer.ClearInput()
    app.composer.SetTurnRunning(True)
    app.attachments.Set(())
    # Captured now, because the next turn replaces them on the app.
    notifications: Channel[ConversationNotification] = Channel()
    approvals: Channel[ApprovalDecision] = Channel()
    cancellation = Cancellation()
    app.notifications = notifications
    app.approvals = approvals
    app.cancellation = cancellation
    app.turn += 1
    return app, (
        waitForNotification(notifications, app.turn),
        runTurn(app, text, notifications, approvals, cancellation),
    )


def runTurn(
    app: App,
    text: str,
    notifications: Channel[ConversationNotification],
    approvals: Channel[ApprovalDecision],
    cancellation: Cancellation,
) -> runtime.Command[Msg]:
    """The command that runs the turn and reports how it ended."""

    async def run() -> Msg:
        err = await app.turnPort.RunTurn(text, notifications, approvals, cancellation)
        return SubmitDone(Err=err)

    return run


def waitForNotification(channel: Channel[ConversationNotification], turn: int) -> runtime.Listener[Msg]:
    """The listener command for one turn's notification channel.

    It ends when the channel closes, which is what stops the chain: a closed
    channel yields no message, and returning ``None`` ends the listener.
    """

    async def listen() -> Msg | None:
        notification = await channel.Get()
        if notification is None:
            return None
        return ConversationNotificationMsg(Notification=notification, Turn=turn)

    return runtime.Listener(listen)


async def _applyOutcome(
    app: App, outcome: commands.Outcome | None, command: tuple[runtime.Command[Msg], ...]
) -> tuple[App, tuple[runtime.Command[Msg], ...]]:
    """Deferred: the package surface imports this module, so it is patched in late."""
    from super_agent.tui import applyOutcome

    return await applyOutcome(app, outcome, command)
