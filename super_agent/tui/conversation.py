"""The boundary between the interactive CLI and the conversation runtime.

The names here are the display DTOs the composition boundary hands the CLI and
the notifications it sends back; the owning feature holds each definition and
this module re-exports it, so callers keep addressing one package.

The port is a bundle rather than one wide service interface: the root keeps only
the snapshot and turn ports, and every other capability is routed to the feature
that owns it.

Two pieces exist for Python's concurrency model:

* :class:`Channel` is a queue with an explicit close. ``asyncio.Queue`` cannot be
  closed, so the close is a flag and ``get`` returns ``None`` once the producer
  has closed and drained the buffer — which is what ends a listener command.
* :class:`Cancellation` is the cancel handle one turn is started with. It is a
  value, so a port that starts before the cancel still sees the cancel.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections import deque
from typing import Any, ClassVar, Final, Protocol

from super_agent.tui.approval import (
    APPROVE_ALWAYS as APPROVE_ALWAYS,
    APPROVE_ONCE as APPROVE_ONCE,
    DENY as DENY_APPROVAL,
    Decision as ApprovalDecision,
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
    ROLE_ASSISTANT as ROLE_ASSISTANT,
    Attachment as MessageAttachment,
    Message as Message,
    Role as Role,
    ToolCall as ToolCall,
)

__all__ = [
    "APPROVE_ALWAYS",
    "APPROVE_ONCE",
    "DENY_APPROVAL",
    "ROLE_ASSISTANT",
    "AgentStatus",
    "AgentStatusChanged",
    "AgentSummary",
    "ApprovalDecision",
    "AttachmentSummary",
    "Cancellation",
    "Channel",
    "ContextUsage",
    "Conversation",
    "ConversationError",
    "ConversationNotification",
    "ConversationView",
    "MCPServerSummary",
    "Message",
    "MessageAppended",
    "MessageAttachment",
    "PermissionRequest",
    "Role",
    "SessionSummary",
    "SnapshotPort",
    "StreamChunkReceived",
    "ToolApprovalCleared",
    "ToolApprovalRequested",
    "ToolCall",
    "TurnPort",
    "UsageReported",
]


@dataclasses.dataclass(frozen=True, slots=True)
class AgentStatus:
    """What the agent is doing, in presentation words rather than runtime states."""

    label: str = "Idle"
    busy: bool = False
    awaiting_approval: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class PermissionRequest:
    """Why a tool call needs a decision, as the menu shows it."""

    tool_name: str = ""
    command: str = ""
    command_class: str = ""
    cwd: str = ""
    touched_paths: tuple[str, ...] = ()
    env_vars: tuple[str, ...] = ()
    reason: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class ContextUsage:
    """What the provider reported the latest model call cost in tokens.

    Absolute counts only: the runtime knows no context-window size, so a fraction
    or a remainder cannot be derived from these.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class ConversationView:
    """One read of conversation state, for a full transcript rebuild."""

    agent_status: AgentStatus = dataclasses.field(default_factory=AgentStatus)
    messages: tuple[Message, ...] = ()
    pending_tool: ToolCall | None = None
    pending_permission: PermissionRequest | None = None
    pending_tool_batch_index: int = 0
    pending_tool_batch_total: int = 0
    streaming_message: Message | None = None


class ConversationNotification:
    """Base class for the sealed conversation-notification set.

    The set is closed by refusing a subclass declared anywhere but this module,
    so the runtime's routing cannot be handed a kind it does not know.
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

    status: AgentStatus = dataclasses.field(default_factory=AgentStatus)


@dataclasses.dataclass(frozen=True, slots=True)
class ToolApprovalRequested(ConversationNotification):
    """The runtime is holding a tool call until the user decides."""

    kind: ClassVar[str] = "ToolApprovalRequested"

    tool_call: ToolCall = dataclasses.field(default_factory=ToolCall)
    request: PermissionRequest = dataclasses.field(default_factory=PermissionRequest)
    batch_index: int = 0
    batch_total: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class ToolApprovalCleared(ConversationNotification):
    """The pending request is gone; nothing is waiting on the user."""

    kind: ClassVar[str] = "ToolApprovalCleared"


@dataclasses.dataclass(frozen=True, slots=True)
class StreamChunkReceived(ConversationNotification):
    """The message being streamed changed."""

    kind: ClassVar[str] = "StreamChunkReceived"

    message: Message | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class MessageAppended(ConversationNotification):
    """One message joined the transcript."""

    kind: ClassVar[str] = "MessageAppended"

    message: Message = dataclasses.field(default_factory=Message)


@dataclasses.dataclass(frozen=True, slots=True)
class UsageReported(ConversationNotification):
    """The provider reported what the latest model call cost in tokens."""

    kind: ClassVar[str] = "UsageReported"

    usage: ContextUsage = dataclasses.field(default_factory=ContextUsage)


@dataclasses.dataclass(frozen=True, slots=True)
class ConversationError(ConversationNotification):
    """The turn failed."""

    kind: ClassVar[str] = "ConversationError"

    err: BaseException | None = None


class Channel[T]:
    """A queue: buffered puts, one consumer, and an explicit close.

    The producer closes it to say "no more will arrive"; a listener command then
    ends. ``asyncio.Queue`` has no close, so the close is a flag and
    :meth:`get` returns ``None`` on it.
    """

    __slots__ = ("_closed", "_items", "_ready")

    def __init__(self) -> None:
        self._items: deque[T] = deque()
        self._closed = False
        self._ready = asyncio.Event()

    def put(self, value: T) -> None:
        """Send one value. A send after the close is dropped, not delivered."""
        if self._closed:
            return
        self._items.append(value)
        self._ready.set()

    def close(self) -> None:
        """Say that no more values will arrive."""
        self._closed = True
        self._ready.set()

    @property
    def closed(self) -> bool:
        """Whether the producer has closed the channel."""
        return self._closed

    async def get(self) -> T | None:
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
    """The cancellation of one turn, as a value rather than a task handle.

    A value because the turn's command may not have started when the user
    cancels: the cancel is already visible by then, and a cancelled
    ``asyncio.Task`` would simply never run.
    """

    __slots__ = ("_cancelled", "_event")

    def __init__(self) -> None:
        self._cancelled = False
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        """Whether the turn has been cancelled."""
        return self._cancelled

    def cancel(self) -> None:
        """Cancel the turn. Idempotent."""
        self._cancelled = True
        self._event.set()

    async def wait(self) -> None:
        """Block until the turn is cancelled, for ports that can race it."""
        await self._event.wait()


class SnapshotPort(Protocol):
    """One read of conversation state."""

    def snapshot(self) -> ConversationView: ...


class TurnPort(Protocol):
    """Running and cancelling the turn in flight."""

    async def run_turn(
        self,
        text: str,
        notifications: Channel[ConversationNotification],
        approvals: Channel[ApprovalDecision],
        cancellation: Cancellation,
    ) -> BaseException | None:
        """Run one turn, reporting as it goes. Returns the failure, if any."""
        ...

    async def cancel(self) -> BaseException | None:
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
    """The composition-boundary bundle :func:`super_agent.tui.new` accepts.

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
    "UsageReported",
    "ConversationError",
)
"""The seven notification kinds, so a test can pin the sealed set."""
