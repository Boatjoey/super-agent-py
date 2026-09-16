"""What the user decided about a risky tool call."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class ApprovalDecision(StrEnum):
    """One approval answer."""

    ApproveOnce = "once"
    ApproveAlways = "always"
    DenyApproval = "deny"


ApproveOnce: Final[ApprovalDecision] = ApprovalDecision.ApproveOnce
ApproveAlways: Final[ApprovalDecision] = ApprovalDecision.ApproveAlways
DenyApproval: Final[ApprovalDecision] = ApprovalDecision.DenyApproval
