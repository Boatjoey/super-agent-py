"""The boundary between the TUI and whatever runs the conversation.

Ported from the Go ``tui/conversation.go``. The names here are the display DTOs
the composition boundary hands the TUI and the notifications it sends back; the
owning feature holds each definition and this module re-exports it, so Go callers
keep addressing one package.

The port is a bundle rather than one wide service interface: the root keeps only
the snapshot and turn ports, and every other capability is routed to the feature
that owns it.

Two Python-shaped pieces have no Go counterpart:

* :class:`Channel` is a Go channel. ``asyncio.Queue`` cannot be closed, so the
  close is an explicit flag and ``Get`` returns ``None`` once the producer has
  closed and drained the buffer — which is what ends a listener command.
* :class:`Cancellation` stands in for the ``context.Context``/``CancelFunc`` pair
  one turn is started with. It is a value, so a port that starts before the
  cancel still sees the cancel, exactly as ``ctx.Err()`` does in Go.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import deque
from typing import Any, ClassVar, Final, Protocol

from super_agent.tui.approval import (
    ApproveAlways as ApproveAlways,
    ApproveOnce as ApproveOnce,
    Decision as ApprovalDecision,
    Deny as DenyApproval,
)
from super_agent.tui.attachments import Item as AttachmentSummary, Port as AttachmentsPort
from super_agent.tui.commands import (
    AgentPort,
    AgentSummary,
    ExtensionPort,
    MCPPort,
    MCPServerSummary,
    MemoryPort,
    PermissionPort,
    SessionPort,
    SessionSummary,
    WorkspacePort,
)
from super_agent.tui.transcript import (
    Attachment as MessageAttachment,
    Message as Message,
    Role as Role,
    RoleAssistant as RoleAssistant,
    ToolCall as ToolCall,
)

__all__ = [
    "AgentStatus",
    "AgentStatusChanged",
    "AgentSummary",
    "ApprovalDecision",
    "ApproveAlways",
    "ApproveOnce",
    "AttachmentSummary",
    "Cancellation",
    "Channel",
    "Conversation",
    "ConversationError",
    "ConversationNotification",
    "ConversationView",
    "DenyApproval",
    "MCPServerSummary",
    "Message",
    "MessageAppended",
    "MessageAttachment",
    "PermissionRequest",
    "Role",
    "RoleAssistant",
    "SessionSummary",
    "SnapshotPort",
    "StreamChunkReceived",
    "ToolApprovalCleared",
    "ToolApprovalRequested",
    "ToolCall",
    "TurnPort",
]


@dataclasses.dataclass(frozen=True, slots=True)
class AgentStatus:
    """What the agent is doing, in presentation words rather than runtime states."""

    Label: str = "Idle"
    Busy: bool = False
    AwaitingApproval: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Why a tool call needs a decision, as the menu shows it."""

    ToolName: str = ""
    Command: str = ""
    CommandClass: str = ""
    CWD: str = ""
    TouchedPaths: tuple[str, ...] = ()
    EnvVars: tuple[str, ...] = ()
    Reason: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class ConversationView:
    """One read of conversation state, for a full transcript rebuild."""

    AgentStatus: AgentStatus = dataclasses.field(default_factory=AgentStatus)
    Messages: tuple[Message, ...] = ()
    PendingTool: ToolCall | None = None
    PendingPermission: PermissionRequest | None = None
    PendingToolBatchIndex: int = 0
    PendingToolBatchTotal: int = 0
    StreamingMessage: Message | None = None


class ConversationNotification:
    """Base class for the sealed conversation-notification set.

    Go closes the set with an unexported marker method; Python closes it by
    refusing a subclass declared anywhere but this module, so the runtime's
    routing cannot be handed a kind it does not know.
    """

    __slots__ = ()

    #: The literal notification name, one per kind.
    kind: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__ != __name__:
            raise TypeError(
                f"{cls.__name__} extends the sealed ConversationNotification set, which may only be "
                f"declared in {__name__}"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class AgentStatusChanged(ConversationNotification):
    """The agent moved between presentation states."""

    kind: ClassVar[str] = "AgentStatusChanged"

    Status: AgentStatus = dataclasses.field(default_factory=AgentStatus)


@dataclasses.dataclass(frozen=True, slots=True)
class ToolApprovalRequested(ConversationNotification):
    """The runtime is holding a tool call until the user decides."""

    kind: ClassVar[str] = "ToolApprovalRequested"

    ToolCall: ToolCall = dataclasses.field(default_factory=ToolCall)
    Request: PermissionRequest = dataclasses.field(default_factory=PermissionRequest)
    BatchIndex: int = 0
    BatchTotal: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class ToolApprovalCleared(ConversationNotification):
    """The pending request is gone; nothing is waiting on the user."""

    kind: ClassVar[str] = "ToolApprovalCleared"


@dataclasses.dataclass(frozen=True, slots=True)
class StreamChunkReceived(ConversationNotification):
    """The message being streamed changed."""

    kind: ClassVar[str] = "StreamChunkReceived"

    Message: Message | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class MessageAppended(ConversationNotification):
    """One message joined the transcript."""

    kind: ClassVar[str] = "MessageAppended"

    Message: Message = dataclasses.field(default_factory=Message)


@dataclasses.dataclass(frozen=True, slots=True)
class ConversationError(ConversationNotification):
    """The turn failed."""

    kind: ClassVar[str] = "ConversationError"

    Err: BaseException | None = None


class Channel[T]:
    """A Go channel: buffered puts, one consumer, and an explicit close.

    Go's ``chan ConversationNotification`` is closed by the producer to say "no
    more will arrive"; a listener command then ends. ``asyncio.Queue`` has no
    close, so the close is a flag and :meth:`Get` returns ``None`` on it.
    """

    __slots__ = ("_closed", "_items", "_ready")

    def __init__(self) -> None:
        self._items: deque[T] = deque()
        self._closed = False
        self._ready = asyncio.Event()

    def Put(self, value: T) -> None:
        """Send one value. A send after the close is dropped, not delivered."""
        if self._closed:
            return
        self._items.append(value)
        self._ready.set()

    def Close(self) -> None:
        """Say that no more values will arrive."""
        self._closed = True
        self._ready.set()

    @property
    def Closed(self) -> bool:
        """Whether the producer has closed the channel."""
        return self._closed

    async def Get(self) -> T | None:
        """The next value, or ``None`` once the channel is closed and drained."""
        while True:
            if self._items:
                return self._items.popleft()
            if self._closed:
                return None
            # Nothing to read and nothing closed: wait. No await sits between the
            # test above and the clear below, so a put cannot be missed.
            self._ready.clear()
            await self._ready.wait()


class Cancellation:
    """The cancellation of one turn: Go's ``context.Context`` and its cancel.

    A value rather than a task handle, because the turn's command may not have
    started when the user cancels: Go's ``ctx.Err()`` is already non-nil by then,
    and a cancelled ``asyncio.Task`` would simply never run.
    """

    __slots__ = ("_cancelled", "_event")

    def __init__(self) -> None:
        self._cancelled = False
        self._event = asyncio.Event()

    @property
    def Cancelled(self) -> bool:
        """Whether the turn has been cancelled."""
        return self._cancelled

    def Cancel(self) -> None:
        """Cancel the turn. Idempotent, like Go's ``CancelFunc``."""
        self._cancelled = True
        self._event.set()

    async def Wait(self) -> None:
        """Block until the turn is cancelled, for ports that can race it."""
        await self._event.wait()


class SnapshotPort(Protocol):
    """One read of conversation state."""

    def Snapshot(self) -> ConversationView: ...


class TurnPort(Protocol):
    """Running and cancelling the turn in flight."""

    async def RunTurn(
        self,
        text: str,
        notifications: Channel[ConversationNotification],
        approvals: Channel[ApprovalDecision],
        cancellation: Cancellation,
    ) -> BaseException | None:
        """Run one turn, reporting as it goes. Returns the failure, if any."""
        ...

    async def Cancel(self) -> BaseException | None:
        """Cancel the turn the runtime is holding, if any."""
        ...


class Conversation(
    SnapshotPort,
    TurnPort,
    SessionPort,
    PermissionPort,
    MCPPort,
    AgentPort,
    MemoryPort,
    WorkspacePort,
    ExtensionPort,
    AttachmentsPort,
    Protocol,
):
    """The composition-boundary bundle :func:`super_agent.tui.New` accepts.

    ``App`` keeps the snapshot and turn ports and routes every other capability
    to the feature that owns it. ``Protocol`` is listed explicitly: a class that
    only inherits protocols is matched nominally without it, so a structurally
    correct adapter would not satisfy the bundle.
    """


NOTIFICATION_KINDS: Final[tuple[str, ...]] = (
    "AgentStatusChanged",
    "ToolApprovalRequested",
    "ToolApprovalCleared",
    "StreamChunkReceived",
    "MessageAppended",
    "ConversationError",
)
"""The six notification kinds, so a test can pin the sealed set."""
