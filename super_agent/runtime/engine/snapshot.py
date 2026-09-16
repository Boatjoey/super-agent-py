"""The read-only view of the engine that callers and observers consume."""

from __future__ import annotations

import dataclasses

from super_agent.runtime.machine import (
    Message,
    PermissionRequest,
    State,
    StateInitializing,
    ToolCall,
)


@dataclasses.dataclass(frozen=True, slots=True)
class EngineView:
    """A consistent snapshot of engine state.

    ``IsBusy`` and ``NeedsInput`` are derived here rather than by each caller, so
    every presentation layer agrees about what "working" and "waiting for you"
    mean.
    """

    State: State = StateInitializing
    Messages: tuple[Message, ...] = ()
    PendingTool: ToolCall | None = None
    PendingPermission: PermissionRequest | None = None
    PendingToolBatchID: str = ""
    PendingToolBatchIndex: int = 0
    PendingToolBatchTotal: int = 0
    StreamingMessage: Message | None = None
    IsBusy: bool = False
    NeedsInput: bool = False
