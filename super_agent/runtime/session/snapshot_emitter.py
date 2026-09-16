"""Turn engine snapshots into the notification stream.

Two jobs, and both are about not repeating yourself: message appends are emitted
once each no matter how often the snapshot is taken, and an approval is announced
once per pending tool even though every snapshot while that tool waits looks
identical.

The emitter is the reason a repeated identical tool call in a batch still prompts
twice: consuming a decision clears ``lastApprovalCall``, so the next snapshot with
a pending tool is a new approval even when the call value is equal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from super_agent.runtime.engine import EngineView
from super_agent.runtime.machine import Message, PermissionRequest, ToolCall
from super_agent.runtime.session.notifications import (
    MessageAppended,
    SessionNotification,
    StateChanged,
    ToolApprovalCleared,
    ToolApprovalRequested,
)

if TYPE_CHECKING:
    import asyncio

    from super_agent.runtime.session.notifications import SessionNotification


class SnapshotEmitter:
    """Deduplicates the notification stream across repeated snapshots."""

    __slots__ = ("emittedMessages", "hasApproval", "lastApprovalCall")

    def __init__(self) -> None:
        self.emittedMessages = 0
        self.hasApproval = False
        self.lastApprovalCall: ToolCall | None = None

    def markApprovalConsumed(self) -> None:
        """Called once a decision is made, before the engine applies it.

        ``hasApproval`` stays set, so a following snapshot without a pending tool
        still emits :class:`ToolApprovalCleared`.
        """
        self.lastApprovalCall = None

    def reset(self, emittedMessages: int) -> None:
        """Re-arm after a context reset, keeping the messages the engine still holds."""
        self.emittedMessages = emittedMessages
        self.hasApproval = False
        self.lastApprovalCall = None

    async def emit(
        self,
        notifications: asyncio.Queue[SessionNotification],
        snapshot: EngineView,
        onMessage: Any = None,
    ) -> None:
        """Publish whatever changed since the last call, in a fixed order.

        State first, then the approval change, then message appends: a consumer
        that renders as it reads must never show an approval against the wrong
        state.
        """
        await notifications.put(StateChanged(state=snapshot.state))

        pending = snapshot.pending_tool
        if pending is not None:
            if not self.hasApproval or self.lastApprovalCall is None or self.lastApprovalCall != pending:
                await notifications.put(
                    ToolApprovalRequested(
                        tool_call=pending,
                        request=permission_request(snapshot),
                        batch_id=snapshot.pending_tool_batch_id,
                        batch_index=snapshot.pending_tool_batch_index,
                        batch_total=snapshot.pending_tool_batch_total,
                    )
                )
                self.lastApprovalCall = pending
                self.hasApproval = True
        else:
            if self.hasApproval:
                await notifications.put(ToolApprovalCleared())
            self.hasApproval = False
            self.lastApprovalCall = None

        messages = snapshot.messages
        if self.emittedMessages > len(messages):
            self.emittedMessages = 0
        for message in messages[self.emittedMessages :]:
            await notifications.put(MessageAppended(message=message))
            if onMessage is not None:
                onMessage(message)
        self.emittedMessages = len(messages)


def permission_request(snapshot: EngineView) -> PermissionRequest:
    permission = snapshot.pending_permission
    return permission if permission is not None else PermissionRequest()


_ = Message
