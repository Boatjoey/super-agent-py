"""The four kinds of work a transition can ask the engine to perform.

Model calls, tool execution, queue processing, and human approval all travel the
same action → result → event path, which is why approval needs no second loop and
no scheduling authority in ``runtime/session``.
"""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar, Final

from super_agent.runtime.permission.types import Request as PermissionRequest
from super_agent.runtime.protocol.types import ToolCall


class ScheduledAction:
    """Base class for the sealed scheduled-action set."""

    __slots__ = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if cls.__module__ != __name__:
            raise TypeError(
                f"{cls.__name__} extends the sealed ScheduledAction set, which may only be declared in {__name__}"
            )


@dataclasses.dataclass(frozen=True, slots=True)
class CallModel(ScheduledAction):
    kind: ClassVar[str] = "CallModel"


@dataclasses.dataclass(frozen=True, slots=True)
class RunTool(ScheduledAction):
    kind: ClassVar[str] = "RunTool"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)


@dataclasses.dataclass(frozen=True, slots=True)
class CheckToolQueue(ScheduledAction):
    kind: ClassVar[str] = "CheckToolQueue"


@dataclasses.dataclass(frozen=True, slots=True)
class AwaitApproval(ScheduledAction):
    kind: ClassVar[str] = "AwaitApproval"

    Call: ToolCall = dataclasses.field(default_factory=ToolCall)
    Request: PermissionRequest = dataclasses.field(default_factory=PermissionRequest)


#: Every scheduled action as a zero value, in declaration order.
AllScheduledActions: Final[tuple[ScheduledAction, ...]] = (
    CallModel(),
    RunTool(),
    CheckToolQueue(),
    AwaitApproval(),
)
