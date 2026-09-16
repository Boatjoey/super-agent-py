"""Engine queries.

Python cannot split one class across modules, so the engine is assembled from
mixins: :class:`QueryMixin` owns Go's ``Engine`` methods that only read state, and
``engine.py`` combines it with the command and action-loop mixins. The
``TYPE_CHECKING`` block declares the attributes ``Engine.__init__`` provides.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from super_agent.runtime.engine.snapshot import EngineView
from super_agent.runtime.machine import (
    Message,
    RoleAssistant,
    State,
    StateAdvancingQueue,
    StateRunningTool,
    StateWaitingApproval,
    StateWaitingLLM,
    ToolCall,
)

if TYPE_CHECKING:
    from super_agent.runtime.machine import RuntimeData


class QueryMixin:
    """Read-only access to the committed runtime data."""

    if TYPE_CHECKING:
        # Provided by Engine.__init__.
        _runtime_data: RuntimeData

    def State(self) -> State:
        return self._runtime_data.State

    def Messages(self) -> list[Message]:
        """A copy of the committed conversation.

        A copy, not the list itself: every caller either sends it to a provider or
        stores it, and neither should be able to reach back into machine state.
        """
        return list(self._runtime_data.Messages)

    def PendingTool(self) -> tuple[ToolCall | None, bool]:
        """The call awaiting approval, and whether there is one."""
        pending = self._runtime_data.PendingTool
        return (pending, True) if pending is not None else (None, False)

    def Snapshot(self) -> EngineView:
        """A consistent view of everything a presentation layer needs."""
        data = self._runtime_data
        busy = data.State in (StateWaitingLLM, StateRunningTool, StateAdvancingQueue)
        streaming: Message | None = None
        if data.StreamingContent != "" or data.StreamingReasoning != "":
            streaming = Message(
                Role=RoleAssistant,
                Content=data.StreamingContent,
                ReasoningContent=data.StreamingReasoning,
            )

        pending = data.PendingTool
        batch_id = ""
        batch_index = 0
        batch_total = 0
        permission = None
        if pending is not None:
            permission = data.PendingPermission
            if data.ToolBatch is not None:
                batch_id = data.ToolBatch.ID
                batch_index = data.ToolBatch.Index
                batch_total = len(data.ToolBatch.Calls)

        return EngineView(
            State=data.State,
            Messages=tuple(data.Messages),
            PendingTool=pending,
            PendingPermission=permission,
            PendingToolBatchID=batch_id,
            PendingToolBatchIndex=batch_index,
            PendingToolBatchTotal=batch_total,
            StreamingMessage=streaming,
            IsBusy=busy,
            NeedsInput=data.State == StateWaitingApproval,
        )
