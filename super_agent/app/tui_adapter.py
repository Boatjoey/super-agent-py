"""The terminal adapter: the runtime application API behind the TUI's port.

Conversion lives at the composition edge, so neither side knows the other: the
TUI sees only its own display DTOs, and the runtime only knows its own
notifications and views.

Two things this file is the single home of:

* the mapping from runtime states and notifications to presentation values. A
  notification kind the mapping does not know becomes a visible
  :class:`tui.ConversationError` naming the type, rather than being dropped, so a
  new runtime notification cannot degrade the interface without a trace.
* the two bridges of a turn. The runtime channels are attached to the TUI's
  channels by coroutines over queues, and the run context is built from the turn's
  :class:`tui.Cancellation` so the runtime still sees one cancellation signal.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses

from super_agent import runtime, tui
from super_agent.app.agents import AgentController
from super_agent.app.mcp import MCPController
from super_agent.errors import JoinedError
from super_agent.runtime.protocol.run_context import RunContext, live_context
from super_agent.runtime.session import ApprovalsClosed, NotificationsClosed

__all__ = [
    "TUIConversation",
    "new_tui_conversation",
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
                    # An unrecognised controller is not an error: a caller may pass
                    # anything, so the default is silent.
                    pass

    # --- agent profiles ------------------------------------------------------

    def list_agents(self) -> list[tui.AgentSummary]:
        """Every configured profile, or nothing when the controller is absent."""
        if self.agents is None:
            return []
        return [
            tui.AgentSummary(
                name=profile.name,
                provider=profile.provider,
                model=profile.model,
                permission_mode=str(profile.permission_mode),
            )
            for profile in self.agents.list()
        ]

    def current_agent(self) -> tui.AgentSummary:
        """The active profile, or an empty summary when there is no controller."""
        if self.agents is None:
            return tui.AgentSummary()
        profile = self.agents.active_profile()
        return tui.AgentSummary(
            name=profile.name,
            provider=profile.provider,
            model=profile.model,
            permission_mode=str(profile.permission_mode),
        )

    async def use_agent(self, name: str) -> None:
        """Switch profiles, replacing the context, model, and visible tools."""
        if self.agents is None:
            raise ValueError("agent profiles are unavailable")
        await self.agents.use(name)

    # --- conversation --------------------------------------------------------

    async def fork(self, title: str) -> str:
        meta = await self.session.fork(title)
        return str(meta.id)

    async def memories(self) -> list[str]:
        return self.session.memories()

    async def remember(self, text: str) -> None:
        await self.session.remember(text)

    async def forget_memories(self) -> None:
        await self.session.forget_memories()

    async def export(self, format: str) -> str:
        return self.session.export(format)

    async def attach(self, path: str) -> tui.AttachmentSummary:
        attachment = self.session.attach(path)
        return tui.AttachmentSummary(name=attachment.name, mime=attachment.mime)

    async def pending_attachments(self) -> tuple[tui.AttachmentSummary, ...]:
        return tuple(
            tui.AttachmentSummary(name=attachment.name, mime=attachment.mime)
            for attachment in self.session.pending_attachments()
        )

    def snapshot(self) -> tui.ConversationView:
        return to_conversation_view(self.session.snapshot())

    async def cancel(self) -> BaseException | None:
        """Cancel the turn in flight, reporting rather than raising a failure."""
        try:
            await self.session.cancel()
        except Exception as error:
            return error
        return None

    async def reset(self) -> None:
        await self.session.reset()

    async def compact(self, summary: str) -> None:
        # The keep-newest default lives in the runtime session.
        await self.session.compact(live_context(), summary, 0)

    async def undo(self) -> None:
        await self.session.undo()

    async def set_permission_mode(self, mode: str) -> None:
        await self.session.set_permission_mode(runtime.PermissionMode(mode))

    def permission_mode(self) -> str:
        return str(self.session.permission_mode())

    def auto_approve_tools(self) -> bool:
        return self.session.auto_approve_tools()

    # --- saved sessions ------------------------------------------------------

    async def list_sessions(self) -> list[tui.SessionSummary]:
        summaries = self.session.list_sessions()
        return [
            tui.SessionSummary(
                id=str(item.id),
                title=item.title,
                provider=item.provider,
                model=item.model,
                cwd=item.cwd,
                parent_id=str(item.parent_id),
            )
            for item in summaries
        ]

    async def resume(self, session_id: str) -> None:
        await self.session.resume(runtime.SessionID(session_id))

    async def rename_session(self, session_id: str, title: str) -> None:
        self.session.rename_session(runtime.SessionID(session_id), title)

    async def delete_session(self, session_id: str) -> None:
        self.session.delete_session(runtime.SessionID(session_id))

    # --- MCP servers ---------------------------------------------------------

    def list_mcp_servers(self) -> list[tui.MCPServerSummary]:
        if self.mcp is None:
            return []
        return [tui.MCPServerSummary(name=server.name, tools=server.tools) for server in self.mcp.list()]

    async def add_mcp_server(self, name: str, command: str, args: list[str]) -> None:
        if self.mcp is None:
            raise ValueError("MCP management is unavailable")
        await self.mcp.add(live_context(), name, command, args)

    async def remove_mcp_server(self, name: str) -> None:
        if self.mcp is None:
            raise ValueError("MCP management is unavailable")
        await self.mcp.remove(name)

    async def restart_mcp_server(self, name: str) -> None:
        if self.mcp is None:
            raise ValueError("MCP management is unavailable")
        await self.mcp.restart(live_context(), name)

    # --- repository and extension queries ------------------------------------

    async def git_diff(self) -> str:
        if self.agents is None:
            raise ValueError("workflow tools are unavailable")
        return await self.agents.git_diff(live_context())

    async def git_status(self) -> str:
        if self.agents is None:
            raise ValueError("workflow tools are unavailable")
        return await self.agents.git_status(live_context())

    async def diagnostics(self, path: str) -> str:
        if self.agents is None:
            raise ValueError("diagnostics are unavailable")
        return await self.agents.diagnostics(live_context(), path)

    def custom_commands(self) -> list[str]:
        if self.agents is None:
            return []
        return self.agents.custom_commands()

    async def expand_custom_command(self, name: str, arguments: str) -> str:
        if self.agents is None:
            raise ValueError("custom commands are unavailable")
        return self.agents.expand_command(name, arguments)

    def skills(self) -> list[str]:
        if self.agents is None:
            return []
        return self.agents.skills()

    def plugins(self) -> list[str]:
        if self.agents is None:
            return []
        return self.agents.plugins()

    # --- the turn ------------------------------------------------------------

    async def run_turn(
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
                    await self.agents.run_hook(ctx, "before_turn")
                except Exception as error:
                    notifications.close()
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
                await self.session.run_turn(ctx, text, runtimeNotifications, runtimeApprovals)
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


def new_tui_conversation(session: runtime.Session, *controllers: object) -> TUIConversation:
    """The session, plus whichever controllers apply."""
    return TUIConversation(session, *controllers)


async def _watchCancellation(cancellation: tui.Cancellation, ctx: RunContext) -> None:
    """Cancel the run context when the interface cancels the turn."""
    await cancellation.wait()
    ctx.cancel()


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
                        await agents.run_hook(ctx, "approval_requested")
                    except Exception as hookError:
                        notifications.put(tui.ConversationError(err=hookError))
                notifications.put(to_conversation_notification(notification))
                continue
            getter.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await getter
            return
    finally:
        notifications.close()


async def _bridgeApprovals(
    approvals: tui.Channel[tui.ApprovalDecision],
    runtimeApprovals: asyncio.Queue[runtime.ApprovalDecision | ApprovalsClosed],
    done: asyncio.Event,
) -> None:
    """Forward the user's decisions to the runtime until the turn ends."""
    while True:
        decision = await approvals.get()
        if decision is None or done.is_set():
            return
        await runtimeApprovals.put(runtime.ApprovalDecision(str(decision)))


async def _hookFailure(agents: AgentController, ctx: RunContext, events: tuple[str, ...]) -> BaseException | None:
    """Run lifecycle hooks, returning their failure instead of raising it."""
    try:
        await agents.run_hooks(ctx, *events)
    except Exception as error:
        return error
    return None


def _join(error: BaseException | None, other: BaseException | None) -> BaseException | None:
    """Join two optional errors: one error, or both joined."""
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
            return tui.AgentStatusChanged(status=to_tui_status(notification.state))
        case runtime.ToolApprovalRequested():
            return tui.ToolApprovalRequested(
                tool_call=to_tui_tool_call(notification.tool_call),
                request=to_tui_permission(notification.request),
                batch_index=notification.batch_index,
                batch_total=notification.batch_total,
            )
        case runtime.ToolApprovalCleared():
            return tui.ToolApprovalCleared()
        case runtime.StreamChunkReceived():
            return tui.StreamChunkReceived(message=to_tui_message_ptr(notification.message))
        case runtime.MessageAppended():
            return tui.MessageAppended(message=to_tui_message(notification.message))
        case runtime.SessionError():
            return tui.ConversationError(err=notification.err)
        case _:
            return tui.ConversationError(
                err=ValueError(f"unknown runtime session notification: {type(notification).__name__}")
            )


def to_conversation_view(view: runtime.EngineView) -> tui.ConversationView:
    """Map one engine snapshot to the view the transcript rebuilds from."""
    result = tui.ConversationView(
        agent_status=to_tui_status(view.state),
        messages=tuple(to_tui_message(message) for message in view.messages),
        pending_tool_batch_index=view.pending_tool_batch_index,
        pending_tool_batch_total=view.pending_tool_batch_total,
        streaming_message=to_tui_message_ptr(view.streaming_message),
    )
    if view.pending_tool is not None:
        result = dataclasses.replace(result, pending_tool=to_tui_tool_call(view.pending_tool))
    if view.pending_permission is not None:
        result = dataclasses.replace(result, pending_permission=to_tui_permission(view.pending_permission))
    return result


def to_tui_status(state: runtime.State) -> tui.AgentStatus:
    """The presentation status for one runtime state.

    The table is exhaustive over the runtime states; a state it does not name is
    reported as ``Unknown`` rather than borrowing a neighbouring label.
    Presentation words, not runtime words, are the whole point: the TUI owns no
    runtime state enum.
    """
    match state:
        case runtime.STATE_INITIALIZING:
            return tui.AgentStatus(label="Initializing", busy=True)
        case runtime.STATE_IDLE:
            return tui.AgentStatus(label="Idle")
        case runtime.STATE_WAITING_LLM:
            return tui.AgentStatus(label="WaitingLLM", busy=True)
        case runtime.STATE_WAITING_APPROVAL:
            return tui.AgentStatus(label="WaitingApproval", awaiting_approval=True)
        case runtime.STATE_RUNNING_TOOL:
            return tui.AgentStatus(label="RunningTool", busy=True)
        case runtime.STATE_ADVANCING_QUEUE:
            return tui.AgentStatus(label="AdvancingQueue", busy=True)
        case _:
            return tui.AgentStatus(label="Unknown")


def to_tui_message_ptr(message: runtime.Message | None) -> tui.Message | None:
    """A message as a presentation value, or ``None`` for no message."""
    if message is None:
        return None
    return to_tui_message(message)


def to_tui_message(message: runtime.Message) -> tui.Message:
    """One message in the transcript's vocabulary.

    The runtime type is a flat tuple with no nil entries, so there is nothing to
    skip.
    """
    calls = tuple(to_tui_tool_call(call) for call in message.tool_calls or ())
    attachments = tuple(
        tui.MessageAttachment(name=attachment.name, mime=attachment.mime) for attachment in message.attachments
    )
    return tui.Message(
        role=tui.Role(str(message.role)),
        content=message.content,
        reasoning_content=message.reasoning_content,
        tool_call_id=message.tool_call_id,
        tool_name=message.tool_name,
        tool_calls=calls,
        interrupted=message.interrupted,
        attachments=attachments,
    )


def to_tui_tool_call(call: runtime.ToolCall) -> tui.ToolCall:
    """One tool call, unchanged: the fields already mean the same thing."""
    return tui.ToolCall(id=call.id, name=call.name, input=call.input)


def to_tui_permission(request: runtime.PermissionRequest) -> tui.PermissionRequest:
    """One permission request, with its sequences copied."""
    return tui.PermissionRequest(
        tool_name=request.tool_name,
        command=request.command,
        command_class=str(request.command_class),
        cwd=request.cwd,
        touched_paths=tuple(request.touched_paths),
        env_vars=tuple(request.env_vars),
        reason=request.reason,
    )
