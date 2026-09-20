"""What the session tells the outside world while a turn runs.

Seven notification kinds carry information. :class:`NotificationsClosed` is the
eighth member and carries none: an :class:`asyncio.Queue` has no close, so the
end of a turn is delivered as a value the consumer stops on.
"""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar, Final

from super_agent.runtime.machine import (
    Message,
    PermissionRequest,
    State,
    StreamChunk,
    ToolCall,
)
from super_agent.runtime.protocol.types import Usage


class SessionNotification:
    """Base class for the sealed session-notification set."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__ != __name__:
            raise TypeError(
                f"{cls.__name__} extends the sealed SessionNotification set, which may only be declared in {__name__}"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class StateChanged(SessionNotification):
    kind: ClassVar[str] = "StateChanged"

    state: State


@dataclasses.dataclass(frozen=True, slots=True)
class ToolApprovalRequested(SessionNotification):
    kind: ClassVar[str] = "ToolApprovalRequested"

    tool_call: ToolCall = dataclasses.field(default_factory=ToolCall)
    request: PermissionRequest = dataclasses.field(default_factory=PermissionRequest)
    batch_id: str = ""
    batch_index: int = 0
    batch_total: int = 0


@dataclasses.dataclass(frozen=True, slots=True)
class ToolApprovalCleared(SessionNotification):
    kind: ClassVar[str] = "ToolApprovalCleared"


@dataclasses.dataclass(frozen=True, slots=True)
class StreamChunkReceived(SessionNotification):
    kind: ClassVar[str] = "StreamChunkReceived"

    chunk: StreamChunk = dataclasses.field(default_factory=StreamChunk)
    #: The whole accumulated streaming message, not just this delta, so a consumer
    #: that drops an intermediate chunk still converges on the same text.
    message: Message | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class MessageAppended(SessionNotification):
    kind: ClassVar[str] = "MessageAppended"

    message: Message = dataclasses.field(default_factory=Message)


@dataclasses.dataclass(frozen=True, slots=True)
class UsageReported(SessionNotification):
    """The provider reported what the latest model call cost in tokens.

    Only the provider's own counts travel here: the runtime carries no
    context-window size, so a fraction of the context cannot be derived.
    """

    kind: ClassVar[str] = "UsageReported"

    usage: Usage = dataclasses.field(default_factory=Usage)


@dataclasses.dataclass(frozen=True, slots=True)
class SessionError(SessionNotification):
    kind: ClassVar[str] = "SessionError"

    err: BaseException | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class NotificationsClosed(SessionNotification):
    """The turn has finished and nothing more will arrive.

    The end-of-turn marker. Delivered as a value because
    :class:`asyncio.Queue` cannot be closed, and kept inside the sealed set so the
    queue has one element type.
    """

    kind: ClassVar[str] = "NotificationsClosed"


#: The single value a consumer stops on. One instance, so a test can compare by
#: identity the way a closed channel is a single event.
NOTIFICATIONS_CLOSED: Final[NotificationsClosed] = NotificationsClosed()
