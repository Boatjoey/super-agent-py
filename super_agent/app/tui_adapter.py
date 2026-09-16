"""The terminal adapter: the runtime application API behind the TUI's port.

Ported from ``app/tui_adapter.go``. Conversion lives at the composition edge, so
neither side knows the other: the TUI sees only its own display DTOs, and the
runtime only knows its own notifications and views.

Two things this file is the single home of:

* the mapping from runtime states and notifications to presentation values. A
  notification kind the mapping does not know becomes a visible
  :class:`tui.ConversationError` naming the type, rather than being dropped, so a
  new runtime notification cannot degrade the interface without a trace.
* the two bridges of a turn. Go attaches the runtime channels to the TUI's
  channels with goroutines; Python's channels are queues, and the run context is
  built from the turn's :class:`tui.Cancellation` so the runtime still sees one
  cancellation signal.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses

from super_agent import runtime, tui
from super_agent.app.agents import AgentController
from super_agent.app.mcp import MCPController
from super_agent.errors import JoinedError
from super_agent.runtime.protocol.run_context import LiveContext, RunContext
from super_agent.runtime.session import ApprovalsClosed, NotificationsClosed

__all__ = [
    "NewTUIConversation",
    "TUIConversation",
    "to_conversation_notification",
    "to_conversation_view",
    "to_tui_message",
    "to_tui_message_ptr",
    "to_tui_permission",
    "to_tui_status",
    "to_tui_tool_call",
]


class TUIConversation:
    """The conversation port the terminal talks to."""

    __slots__ = ("agents", "mcp", "session")

    def __init__(self, session: runtime.Session, *controllers: object) -> None:
        self.session = session
        self.mcp: MCPController | None = None
        self.agents: AgentController | None = None
        for controller in controllers:
            match controller:
                case MCPController():
                    self.mcp = controller
                case AgentController():
                    self.agents = controller
                case _:
                    # An unrecognised controller is not an error: Go's type switch
                    # has the same silent default, and a caller may pass anything.
                    pass

    # --- agent profiles ------------------------------------------------------

    def ListAgents(self) -> list[tui.AgentSummary]:
        """Every configured profile, or nothing when the controller is absent."""
        if self.agents is None:
            return []
        return [
            tui.AgentSummary(
                Name=profile.Name,
                Provider=profile.Provider,
                Model=profile.Model,
                PermissionMode=str(profile.PermissionMode),
            )
            for profile in self.agents.List()
        ]

    def CurrentAgent(self) -> tui.AgentSummary:
        """The active profile, or an empty summary when there is no controller."""
        if self.agents is None:
            return tui.AgentSummary()
        profile = self.agents.Current()
        return tui.AgentSummary(
            Name=profile.Name,
            Provider=profile.Provider,
            Model=profile.Model,
            PermissionMode=str(profile.PermissionMode),
        )

    async def UseAgent(self, name: str) -> None:
        """Switch profiles, replacing the context, model, and visible tools."""
        if self.agents is None:
            raise ValueError("agent profiles are unavailable")
        await self.agents.Use(name)

    # --- conversation --------------------------------------------------------

    async def Fork(self, title: str) -> str:
        meta = await self.session.Fork(title)
        return str(meta.ID)

    async def Memories(self) -> list[str]:
        return self.session.Memories()

    async def Remember(self, text: str) -> None:
        await self.session.Remember(text)

    async def ForgetMemories(self) -> None:
        await self.session.ForgetMemories()

    async def Export(self, format: str) -> str:
        return self.session.Export(format)

    async def Attach(self, path: str) -> tui.AttachmentSummary:
        attachment = self.session.Attach(path)
        return tui.AttachmentSummary(Name=attachment.Name, MIME=attachment.MIME)

    async def PendingAttachments(self) -> tuple[tui.AttachmentSummary, ...]:
        return tuple(
            tui.AttachmentSummary(Name=attachment.Name, MIME=attachment.MIME)
            for attachment in self.session.PendingAttachments()
        )

    def Snapshot(self) -> tui.ConversationView:
        return to_conversation_view(self.session.Snapshot())

    async def Cancel(self) -> BaseException | None:
        """Cancel the turn in flight, reporting rather than raising a failure."""
        try:
            await self.session.Cancel()
        except Exception as error:
            return error
        return None

    async def Reset(self) -> None:
        await self.session.Reset()

    async def Compact(self, summary: str) -> None:
        # The keep-newest default lives in the runtime session.
        await self.session.Compact(LiveContext(), summary, 0)

    async def Undo(self) -> None:
        await self.session.Undo()

    async def SetPermissionMode(self, mode: str) -> None:
        await self.session.SetPermissionMode(runtime.PermissionMode(mode))

    def PermissionMode(self) -> str:
        return str(self.session.PermissionMode())

    def AutoApproveTools(self) -> bool:
        return self.session.AutoApproveTools()

    # --- saved sessions ------------------------------------------------------

    async def ListSessions(self) -> list[tui.SessionSummary]:
        summaries = self.session.ListSessions()
        return [
            tui.SessionSummary(
                ID=str(item.ID),
                Title=item.Title,
                Provider=item.Provider,
                Model=item.Model,
                CWD=item.CWD,
                ParentID=str(item.ParentID),
            )
            for item in summaries
        ]

    async def Resume(self, session_id: str) -> None:
        await self.session.Resume(runtime.SessionID(session_id))

    async def RenameSession(self, session_id: str, title: str) -> None:
        self.session.RenameSession(runtime.SessionID(session_id), title)

    async def DeleteSession(self, session_id: str) -> None:
        self.session.DeleteSession(runtime.SessionID(session_id))

    # --- MCP servers ---------------------------------------------------------

    def ListMCPServers(self) -> list[tui.MCPServerSummary]:
        if self.mcp is None:
            return []
        return [tui.MCPServerSummary(Name=server.Name, Tools=server.Tools) for server in self.mcp.List()]

    async def AddMCPServer(self, name: str, command: str, args: list[str]) -> None:
        if self.mcp is None:
            raise ValueError("MCP management is unavailable")
        await self.mcp.Add(LiveContext(), name, command, args)

    async def RemoveMCPServer(self, name: str) -> None:
        if self.mcp is None:
            raise ValueError("MCP management is unavailable")
        await self.mcp.Remove(name)

    async def RestartMCPServer(self, name: str) -> None:
        if self.mcp is None:
            raise ValueError("MCP management is unavailable")
        await self.mcp.Restart(LiveContext(), name)

    # --- repository and extension queries ------------------------------------

    async def GitDiff(self) -> str:
        if self.agents is None:
            raise ValueError("workflow tools are unavailable")
        return await self.agents.GitDiff(LiveContext())

    async def GitStatus(self) -> str:
        if self.agents is None:
            raise ValueError("workflow tools are unavailable")
        return await self.agents.GitStatus(LiveContext())

    async def Diagnostics(self, path: str) -> str:
        if self.agents is None:
            raise ValueError("diagnostics are unavailable")
        return await self.agents.Diagnostics(LiveContext(), path)

    def CustomCommands(self) -> list[str]:
        if self.agents is None:
            return []
        return self.agents.CustomCommands()

    async def ExpandCustomCommand(self, name: str, arguments: str) -> str:
        if self.agents is None:
            raise ValueError("custom commands are unavailable")
        return self.agents.ExpandCommand(name, arguments)

    def Skills(self) -> list[str]:
        if self.agents is None:
            return []
        return self.agents.Skills()

    def Plugins(self) -> list[str]:
        if self.agents is None:
            return []
        return self.agents.Plugins()

    # --- the turn ------------------------------------------------------------

    async def RunTurn(
        self,
        text: str,
        notifications: tui.Channel[tui.ConversationNotification],
        approvals: tui.Channel[tui.ApprovalDecision],
        cancellation: tui.Cancellation,
    ) -> BaseException | None:
        """Run one turn, bridging the runtime's queues to the interface's channels.

        Returns the failure instead of raising it: the TUI's command reports it
        through ``SubmitDone``, and an interface that has already gone away must
        not be hit with an exception on its way out.
        """
        ctx = RunContext()
        watcher = asyncio.create_task(_watchCancellation(cancellation, ctx))
        try:
            if self.agents is not None:
                try:
                    await self.agents.RunHook(ctx, "before_turn")
                except Exception as error:
                    notifications.Close()
                    return error
            runtimeNotifications: asyncio.Queue[runtime.SessionNotification] = asyncio.Queue(maxsize=100)
            runtimeApprovals: asyncio.Queue[runtime.ApprovalDecision | ApprovalsClosed] = asyncio.Queue(maxsize=1)
            done = asyncio.Event()
            notificationBridge = asyncio.create_task(
                _bridgeNotifications(self.agents, ctx, notifications, runtimeNotifications, done)
            )
            approvalBridge = asyncio.create_task(_bridgeApprovals(approvals, runtimeApprovals, done))
            error: BaseException | None = None
            try:
                await self.session.RunTurn(ctx, text, runtimeNotifications, runtimeApprovals)
            except Exception as failure:
                error = failure
            finally:
                done.set()
                approvalBridge.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await approvalBridge
                # The notification bridge drains what the turn produced and then
                # closes the channel, which is what ends the interface's listener.
                await notificationBridge
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        if self.agents is not None:
            hookCtx = RunContext()
            if error is not None:
                error = _join(error, await _hookFailure(self.agents, hookCtx, ("error",)))
            error = _join(error, await _hookFailure(self.agents, hookCtx, ("turn_complete", "after_turn")))
        return error


def NewTUIConversation(session: runtime.Session, *controllers: object) -> TUIConversation:
    """Go's ``NewTUIConversation``: the session, plus whichever controllers apply."""
    return TUIConversation(session, *controllers)


async def _watchCancellation(cancellation: tui.Cancellation, ctx: RunContext) -> None:
    """Cancel the run context when the interface cancels the turn."""
    await cancellation.Wait()
    ctx.Cancel()


async def _bridgeNotifications(
    agents: AgentController | None,
    ctx: RunContext,
    notifications: tui.Channel[tui.ConversationNotification],
    runtimeNotifications: asyncio.Queue[runtime.SessionNotification],
    done: asyncio.Event,
) -> None:
    """Forward runtime notifications, closing the channel when the turn ends.

    It stops when the runtime closes the queue *or* when the turn is over and the
    queue has drained, so an event the runtime could not enqueue cannot leave the
    interface's listener waiting forever.
    """
    try:
        while True:
            getter = asyncio.ensure_future(runtimeNotifications.get())
            stopper = asyncio.ensure_future(done.wait())
            finished, _pending = await asyncio.wait({getter, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if getter in finished:
                notification = getter.result()
                if not stopper.done():
                    stopper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stopper
                if isinstance(notification, NotificationsClosed):
                    return
                if isinstance(notification, runtime.ToolApprovalRequested) and agents is not None:
                    try:
                        await agents.RunHook(ctx, "approval_requested")
                    except Exception as hookError:
                        notifications.Put(tui.ConversationError(Err=hookError))
                notifications.Put(to_conversation_notification(notification))
                continue
            getter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await getter
            return
    finally:
        notifications.Close()


async def _bridgeApprovals(
    approvals: tui.Channel[tui.ApprovalDecision],
    runtimeApprovals: asyncio.Queue[runtime.ApprovalDecision | ApprovalsClosed],
    done: asyncio.Event,
) -> None:
    """Forward the user's decisions to the runtime until the turn ends."""
    while True:
        decision = await approvals.Get()
        if decision is None or done.is_set():
            return
        await runtimeApprovals.put(runtime.ApprovalDecision(str(decision)))


async def _hookFailure(agents: AgentController, ctx: RunContext, events: tuple[str, ...]) -> BaseException | None:
    """Run lifecycle hooks, returning their failure instead of raising it."""
    try:
        await agents.RunHooks(ctx, *events)
    except Exception as error:
        return error
    return None


def _join(error: BaseException | None, other: BaseException | None) -> BaseException | None:
    """Go's ``errors.Join`` for two values: one error, or both joined."""
    if error is None:
        return other
    if other is None:
        return error
    return JoinedError(error, other)


# --- the mapping table -------------------------------------------------------


def to_conversation_notification(
    notification: runtime.SessionNotification,
) -> tui.ConversationNotification:
    """Map one runtime notification to the interface's own kind.

    An unknown kind becomes a :class:`tui.ConversationError` naming its type.
    Dropping it would let a new runtime notification degrade the interface with
    nothing to show for it, which is the failure this default exists to prevent.
    """
    match notification:
        case runtime.StateChanged():
            return tui.AgentStatusChanged(Status=to_tui_status(notification.State))
        case runtime.ToolApprovalRequested():
            return tui.ToolApprovalRequested(
                ToolCall=to_tui_tool_call(notification.ToolCall),
                Request=to_tui_permission(notification.Request),
                BatchIndex=notification.BatchIndex,
                BatchTotal=notification.BatchTotal,
            )
        case runtime.ToolApprovalCleared():
            return tui.ToolApprovalCleared()
        case runtime.StreamChunkReceived():
            return tui.StreamChunkReceived(Message=to_tui_message_ptr(notification.Message))
        case runtime.MessageAppended():
            return tui.MessageAppended(Message=to_tui_message(notification.Message))
        case runtime.SessionError():
            return tui.ConversationError(Err=notification.Err)
        case _:
            return tui.ConversationError(
                Err=ValueError(f"unknown runtime session notification: {type(notification).__name__}")
            )


def to_conversation_view(view: runtime.EngineView) -> tui.ConversationView:
    """Map one engine snapshot to the view the transcript rebuilds from."""
    result = tui.ConversationView(
        AgentStatus=to_tui_status(view.State),
        Messages=tuple(to_tui_message(message) for message in view.Messages),
        PendingToolBatchIndex=view.PendingToolBatchIndex,
        PendingToolBatchTotal=view.PendingToolBatchTotal,
        StreamingMessage=to_tui_message_ptr(view.StreamingMessage),
    )
    if view.PendingTool is not None:
        result = dataclasses.replace(result, PendingTool=to_tui_tool_call(view.PendingTool))
    if view.PendingPermission is not None:
        result = dataclasses.replace(result, PendingPermission=to_tui_permission(view.PendingPermission))
    return result


def to_tui_status(state: runtime.State) -> tui.AgentStatus:
    """The presentation status for one runtime state.

    The table is exhaustive over the runtime states; a state it does not name is
    reported as ``Unknown`` rather than borrowing a neighbouring label, which is
    what Go's switch default does. Presentation words, not runtime words, are the
    whole point: the TUI owns no runtime state enum.
    """
    match state:
        case runtime.StateInitializing:
            return tui.AgentStatus(Label="Initializing", Busy=True)
        case runtime.StateIdle:
            return tui.AgentStatus(Label="Idle")
        case runtime.StateWaitingLLM:
            return tui.AgentStatus(Label="WaitingLLM", Busy=True)
        case runtime.StateWaitingApproval:
            return tui.AgentStatus(Label="WaitingApproval", AwaitingApproval=True)
        case runtime.StateRunningTool:
            return tui.AgentStatus(Label="RunningTool", Busy=True)
        case runtime.StateAdvancingQueue:
            return tui.AgentStatus(Label="AdvancingQueue", Busy=True)
        case _:
            return tui.AgentStatus(Label="Unknown")


def to_tui_message_ptr(message: runtime.Message | None) -> tui.Message | None:
    """A message as a presentation value, or ``None`` for no message."""
    if message is None:
        return None
    return to_tui_message(message)


def to_tui_message(message: runtime.Message) -> tui.Message:
    """One message in the transcript's vocabulary.

    Go's message holds ``[]*ToolCall`` and skips nil entries; the runtime type here
    is a flat tuple with no nil in it, so there is nothing to skip.
    """
    calls = tuple(to_tui_tool_call(call) for call in message.ToolCalls or ())
    attachments = tuple(
        tui.MessageAttachment(Name=attachment.Name, MIME=attachment.MIME) for attachment in message.Attachments
    )
    return tui.Message(
        Role=tui.Role(str(message.Role)),
        Content=message.Content,
        ReasoningContent=message.ReasoningContent,
        ToolCallID=message.ToolCallID,
        ToolName=message.ToolName,
        ToolCalls=calls,
        Interrupted=message.Interrupted,
        Attachments=attachments,
    )


def to_tui_tool_call(call: runtime.ToolCall) -> tui.ToolCall:
    """One tool call, unchanged: the fields already mean the same thing."""
    return tui.ToolCall(ID=call.ID, Name=call.Name, Input=call.Input)


def to_tui_permission(request: runtime.PermissionRequest) -> tui.PermissionRequest:
    """One permission request, with its sequences copied."""
    return tui.PermissionRequest(
        ToolName=request.ToolName,
        Command=request.Command,
        CommandClass=str(request.CommandClass),
        CWD=request.CWD,
        TouchedPaths=tuple(request.TouchedPaths),
        EnvVars=tuple(request.EnvVars),
        Reason=request.Reason,
    )
