"""Engine queries.

Python cannot split one class across modules, so the engine is assembled from
mixins: :class:`QueryMixin` owns the ``Engine`` methods that only read state, and
``engine.py`` combines it with the command and action-loop mixins. The
``TYPE_CHECKING`` block declares the attributes ``Engine.__init__`` provides.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from super_agent.runtime.engine.snapshot import EngineView
from super_agent.runtime.machine import (
    ROLE_ASSISTANT,
    STATE_ADVANCING_QUEUE,
    STATE_RUNNING_TOOL,
    STATE_WAITING_APPROVAL,
    STATE_WAITING_LLM,
    Message,
    State,
    ToolCall,
)

if TYPE_CHECKING:
    from super_agent.runtime.machine import RuntimeData


class QueryMixin:
    """Read-only access to the committed runtime data."""

    if TYPE_CHECKING:
        # Provided by Engine.__init__.
        _runtime_data: RuntimeData

    def state(self) -> State:
        return self._runtime_data.state

    def messages(self) -> list[Message]:
        """A copy of the committed conversation.

        A copy, not the list itself: every caller either sends it to a provider or
        stores it, and neither should be able to reach back into machine state.
        """
        return list(self._runtime_data.messages)

    def pending_tool(self) -> tuple[ToolCall | None, bool]:
        """The call awaiting approval, and whether there is one."""
        pending = self._runtime_data.pending_tool
        return (pending, True) if pending is not None else (None, False)

    def snapshot(self) -> EngineView:
        """A consistent view of everything a presentation layer needs."""
        data = self._runtime_data
        busy = data.state in (STATE_WAITING_LLM, STATE_RUNNING_TOOL, STATE_ADVANCING_QUEUE)
        streaming: Message | None = None
        if data.streaming_content != "" or data.streaming_reasoning != "":
            streaming = Message(
                role=ROLE_ASSISTANT,
                content=data.streaming_content,
                reasoning_content=data.streaming_reasoning,
            )

        pending = data.pending_tool
        batch_id = ""
        batch_index = 0
        batch_total = 0
        permission = None
        if pending is not None:
            permission = data.pending_permission
            if data.tool_batch is not None:
                batch_id = data.tool_batch.id
                batch_index = data.tool_batch.index
                batch_total = len(data.tool_batch.calls)

        return EngineView(
            state=data.state,
            messages=tuple(data.messages),
            pending_tool=pending,
            pending_permission=permission,
            pending_tool_batch_id=batch_id,
            pending_tool_batch_index=batch_index,
            pending_tool_batch_total=batch_total,
            streaming_message=streaming,
            is_busy=busy,
            needs_input=data.state == STATE_WAITING_APPROVAL,
        )
