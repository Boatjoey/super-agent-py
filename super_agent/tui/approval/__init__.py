"""The tool-approval feature.

The package surface, re-exported so callers keep writing
``approval.Model`` and ``approval.APPROVE_ONCE``.
"""

from __future__ import annotations

from super_agent.tui.approval.model import (
    APPROVE_ALWAYS as APPROVE_ALWAYS,
    APPROVE_ONCE as APPROVE_ONCE,
    DENY as DENY,
    Decision as Decision,
    Model as Model,
    Request as Request,
)

__all__ = ["APPROVE_ALWAYS", "APPROVE_ONCE", "DENY", "Decision", "Model", "Request"]
