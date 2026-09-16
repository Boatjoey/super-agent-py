"""What a scheduled action produced, before it is mapped to an event."""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar

from super_agent.runtime.machine.approval import ApprovalDecision
from super_agent.runtime.protocol.types import ModelResponse, ToolCall


class ScheduledActionResult:
    """Base class for the sealed scheduled-action-result set."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__ != __name__:
            raise TypeError(
                f"{cls.__name__} extends the sealed ScheduledActionResult set, which may only be declared in {__name__}"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class ModelReplied(ScheduledActionResult):
    kind: ClassVar[str] = "ModelReplied"

    Response: ModelResponse = dataclasses.field(default_factory=ModelResponse)


@dataclasses.dataclass(frozen=True, slots=True)
class ToolFinished(ScheduledActionResult):
    kind: ClassVar[str] = "ToolFinished"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)
    Result: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class ToolQueueChecked(ScheduledActionResult):
    kind: ClassVar[str] = "ToolQueueChecked"


@dataclasses.dataclass(frozen=True, slots=True)
class ApprovalReceived(ScheduledActionResult):
    kind: ClassVar[str] = "ApprovalReceived"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)
    Decision: ApprovalDecision = ApprovalDecision.ApproveOnce


#: Every result type as a zero value, in declaration order.
AllScheduledActionResults: tuple[ScheduledActionResult, ...] = (
    ModelReplied(),
    ToolFinished(),
    ToolQueueChecked(),
    ApprovalReceived(),
)
