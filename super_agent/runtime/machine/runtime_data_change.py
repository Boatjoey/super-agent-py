"""The thirteen runtime-data changes a transition can request.

Each is a frozen value. A change describes what to do; the applier is what does
it, on a clone, in order, followed by a fresh validation.
"""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar, Final

from super_agent.runtime.permission.types import Request as PermissionRequest
from super_agent.runtime.protocol.types import Attachment, Message, StreamChunk, ToolCall


class RuntimeDataChange:
    """Base class for the sealed runtime-data-change set."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__ != __name__:
            raise TypeError(
                f"{cls.__name__} extends the sealed RuntimeDataChange set, which may only be declared in {__name__}"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class AppendUserMessage(RuntimeDataChange):
    kind: ClassVar[str] = "AppendUserMessage"

    content: str = ""
    attachments: tuple[Attachment, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class AppendAssistantMessage(RuntimeDataChange):
    kind: ClassVar[str] = "AppendAssistantMessage"

    message: Message = dataclasses.field(default_factory=Message)


@dataclasses.dataclass(frozen=True, slots=True)
class AppendToolResult(RuntimeDataChange):
    kind: ClassVar[str] = "AppendToolResult"

    call: ToolCall = dataclasses.field(default_factory=ToolCall)
    result: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class AppendStreamingAssistant(RuntimeDataChange):
    kind: ClassVar[str] = "AppendStreamingAssistant"

    chunk: StreamChunk = dataclasses.field(default_factory=StreamChunk)


@dataclasses.dataclass(frozen=True, slots=True)
class FlushStreamingAssistant(RuntimeDataChange):
    kind: ClassVar[str] = "FlushStreamingAssistant"

    interrupted: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class SetPendingTool(RuntimeDataChange):
    kind: ClassVar[str] = "SetPendingTool"

    call: ToolCall = dataclasses.field(default_factory=ToolCall)
    request: PermissionRequest = dataclasses.field(default_factory=PermissionRequest)


@dataclasses.dataclass(frozen=True, slots=True)
class SetToolCallBatch(RuntimeDataChange):
    kind: ClassVar[str] = "SetToolCallBatch"

    id: str = ""
    calls: tuple[ToolCall, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class AdvanceToolCallBatch(RuntimeDataChange):
    kind: ClassVar[str] = "AdvanceToolCallBatch"


@dataclasses.dataclass(frozen=True, slots=True)
class ClearPendingTool(RuntimeDataChange):
    kind: ClassVar[str] = "ClearPendingTool"


@dataclasses.dataclass(frozen=True, slots=True)
class SetCurrentTool(RuntimeDataChange):
    kind: ClassVar[str] = "SetCurrentTool"

    call: ToolCall = dataclasses.field(default_factory=ToolCall)


@dataclasses.dataclass(frozen=True, slots=True)
class ClearCurrentTool(RuntimeDataChange):
    kind: ClassVar[str] = "ClearCurrentTool"


@dataclasses.dataclass(frozen=True, slots=True)
class ClearToolCallBatch(RuntimeDataChange):
    kind: ClassVar[str] = "ClearToolCallBatch"


@dataclasses.dataclass(frozen=True, slots=True)
class ResetConversation(RuntimeDataChange):
    kind: ClassVar[str] = "ResetConversation"


#: Every change type as a zero value, in declaration order.
ALL_RUNTIME_DATA_CHANGES: Final[tuple[RuntimeDataChange, ...]] = (
    AppendUserMessage(),
    AppendAssistantMessage(),
    AppendToolResult(),
    AppendStreamingAssistant(),
    FlushStreamingAssistant(),
    SetPendingTool(),
    SetToolCallBatch(),
    AdvanceToolCallBatch(),
    ClearPendingTool(),
    SetCurrentTool(),
    ClearCurrentTool(),
    ClearToolCallBatch(),
    ResetConversation(),
)
