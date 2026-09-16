"""The fifteen events the machine accepts.

The set is closed by construction:
:meth:`Event.__init_subclass__` refuses a subclass declared anywhere but this
module, so the transition registry can never be extended from outside.

Each event declares its kind as a class attribute rather than a method, which
makes ``type(event).kind`` the registry key's second half and lets static
analysis see the literal.
"""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar, Final

from super_agent.runtime.permission.types import Request as PermissionRequest
from super_agent.runtime.protocol.types import Attachment, ModelResponse, ToolCall


class Event:
    """Base class for the sealed event set."""

    __slots__ = ()

    #: The literal event name; one half of a transition registry key.
    kind: ClassVar[str]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__ != __name__:
            raise TypeError(f"{cls.__name__} extends the sealed Event set, which may only be declared in {__name__}")


@dataclasses.dataclass(frozen=True, slots=True)
class UserMessageSubmitted(Event):
    kind: ClassVar[str] = "UserMessageSubmitted"

    Content: str = ""
    Attachments: tuple[Attachment, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class AssistantMessageReceived(Event):
    kind: ClassVar[str] = "AssistantMessageReceived"

    Response: ModelResponse = dataclasses.field(default_factory=ModelResponse)


@dataclasses.dataclass(frozen=True, slots=True)
class ToolBatchReceived(Event):
    kind: ClassVar[str] = "ToolBatchReceived"

    Content: str = ""
    Calls: tuple[ToolCall, ...] = ()
    ReasoningContent: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class ToolCallNeedsApproval(Event):
    kind: ClassVar[str] = "ToolCallNeedsApproval"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)
    Request: PermissionRequest = dataclasses.field(default_factory=PermissionRequest)


@dataclasses.dataclass(frozen=True, slots=True)
class ToolCallReadyToRun(Event):
    kind: ClassVar[str] = "ToolCallReadyToRun"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)


@dataclasses.dataclass(frozen=True, slots=True)
class ToolCallDenied(Event):
    kind: ClassVar[str] = "ToolCallDenied"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)
    Reason: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class ToolBatchFinished(Event):
    kind: ClassVar[str] = "ToolBatchFinished"


@dataclasses.dataclass(frozen=True, slots=True)
class ToolResultReceived(Event):
    kind: ClassVar[str] = "ToolResultReceived"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)
    Result: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class ApprovalGranted(Event):
    kind: ClassVar[str] = "ApprovalGranted"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)


@dataclasses.dataclass(frozen=True, slots=True)
class ApprovalAlwaysGranted(Event):
    kind: ClassVar[str] = "ApprovalAlwaysGranted"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)


@dataclasses.dataclass(frozen=True, slots=True)
class ApprovalDenied(Event):
    kind: ClassVar[str] = "ApprovalDenied"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)


@dataclasses.dataclass(frozen=True, slots=True)
class ErrorOccurred(Event):
    kind: ClassVar[str] = "ErrorOccurred"

    Err: BaseException | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class CancelRequested(Event):
    kind: ClassVar[str] = "CancelRequested"


@dataclasses.dataclass(frozen=True, slots=True)
class ResetRequested(Event):
    kind: ClassVar[str] = "ResetRequested"


@dataclasses.dataclass(frozen=True, slots=True)
class EngineReady(Event):
    kind: ClassVar[str] = "EngineReady"


#: Every event type as a zero value, in declaration order. Registration,
#: serialization, and the conformance test all iterate this.
AllEvents: Final[tuple[Event, ...]] = (
    UserMessageSubmitted(),
    AssistantMessageReceived(),
    ToolBatchReceived(),
    ToolCallNeedsApproval(),
    ToolCallReadyToRun(),
    ToolCallDenied(),
    ToolBatchFinished(),
    ToolResultReceived(),
    ApprovalGranted(),
    ApprovalAlwaysGranted(),
    ApprovalDenied(),
    ErrorOccurred(),
    CancelRequested(),
    ResetRequested(),
    EngineReady(),
)
