"""The read-only view of the engine that callers and observers consume."""

from __future__ import annotations

import dataclasses

from super_agent.runtime.machine import (
    STATE_INITIALIZING,
    Message,
    PermissionRequest,
    State,
    ToolCall,
)


@dataclasses.dataclass(frozen=True, slots=True)
class EngineView:
    """A consistent snapshot of engine state.

    ``IsBusy`` and ``NeedsInput`` are derived here rather than by each caller, so
    every presentation layer agrees about what "working" and "waiting for you"
    mean.
    """

    state: State = STATE_INITIALIZING
    messages: tuple[Message, ...] = ()
    pending_tool: ToolCall | None = None
    pending_permission: PermissionRequest | None = None
    pending_tool_batch_id: str = ""
    pending_tool_batch_index: int = 0
    pending_tool_batch_total: int = 0
    streaming_message: Message | None = None
    is_busy: bool = False
    needs_input: bool = False
