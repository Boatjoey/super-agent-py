"""Durability hooks the rest of the session calls.

Each of these is best effort on purpose: a transcript that cannot be written must
not fail the turn the user is watching. The two places where a *failed* write must
fail the operation — reset and compaction — persist before mutating instead of
calling these.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from super_agent.runtime.machine import ApprovalDecision, Message, ToolCall

if TYPE_CHECKING:
    from super_agent.runtime.session.repository import Repository, SessionID


class PersistenceMixin:
    """Write-through helpers for the stored transcript."""

    if TYPE_CHECKING:
        repository: Repository | None

        def metaID(self) -> SessionID: ...

    def persistTurnBoundary(self) -> None:
        """Start a new turn id, so a resumed transcript can be grouped by turn."""
        if self.repository is not None:
            self.repository.AssignNewTurnID(self.metaID())

    def persistMessage(self, message: Message) -> None:
        if self.repository is not None:
            self.repository.SaveMessage(self.metaID(), message)

    def persistApproval(self, decision: ApprovalDecision, call: ToolCall) -> None:
        """Approval decisions are audited and never sent to a model."""
        if self.repository is None:
            return
        self.repository.SaveApproval(self.metaID(), decision, call)

    def persistError(self, error: BaseException | None) -> None:
        if self.repository is not None and error is not None:
            self.repository.SaveError(self.metaID(), error)
