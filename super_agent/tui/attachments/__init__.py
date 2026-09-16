"""The attachments feature.

The package surface, re-exported so callers keep writing
``attachments.Model`` and ``attachments.Item``.
"""

from __future__ import annotations

from super_agent.tui.attachments.model import (
    AttachCommand as AttachCommand,
    Attached as Attached,
    Item as Item,
    Loaded as Loaded,
    Model as Model,
    New as New,
    Outcome as Outcome,
    Port as Port,
)

__all__ = ["AttachCommand", "Attached", "Item", "Loaded", "Model", "New", "Outcome", "Port"]
