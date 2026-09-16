"""What the user decided about a risky tool call."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class ApprovalDecision(StrEnum):
    """One approval answer."""

    APPROVE_ONCE = "once"
    APPROVE_ALWAYS = "always"
    DENY_APPROVAL = "deny"


APPROVE_ONCE: Final[ApprovalDecision] = ApprovalDecision.APPROVE_ONCE
APPROVE_ALWAYS: Final[ApprovalDecision] = ApprovalDecision.APPROVE_ALWAYS
DENY_APPROVAL: Final[ApprovalDecision] = ApprovalDecision.DENY_APPROVAL
