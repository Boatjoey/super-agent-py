"""The tool-approval feature.

Go's ``tui/approval`` package surface, re-exported so callers keep writing
``approval.Model`` and ``approval.ApproveOnce``.
"""

from __future__ import annotations

from super_agent.tui.approval.model import (
    ApproveAlways as ApproveAlways,
    ApproveOnce as ApproveOnce,
    Decision as Decision,
    Deny as Deny,
    Model as Model,
    Request as Request,
)

__all__ = ["ApproveAlways", "ApproveOnce", "Decision", "Deny", "Model", "Request"]
