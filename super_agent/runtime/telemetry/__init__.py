"""Process-wide JSONL telemetry.

A package spans several modules here, so this module stands in for the package
namespace; see :mod:`super_agent.runtime.telemetry.telemetry` for the sink.
"""

from __future__ import annotations

from super_agent.runtime.telemetry.telemetry import (
    Close as Close,
    Configure as Configure,
    Fields as Fields,
    IDs as IDs,
    IDsFrom as IDsFrom,
    IsConfigured as IsConfigured,
    Record as Record,
    WithIDs as WithIDs,
)

__all__ = [
    "Close",
    "Configure",
    "Fields",
    "IDs",
    "IDsFrom",
    "IsConfigured",
    "Record",
    "WithIDs",
]
