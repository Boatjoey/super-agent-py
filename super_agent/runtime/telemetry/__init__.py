"""Process-wide JSONL telemetry.

A package spans several modules here, so this module stands in for the package
namespace; see :mod:`super_agent.runtime.telemetry.telemetry` for the sink.
"""

from __future__ import annotations

from super_agent.runtime.telemetry.telemetry import (
    Fields as Fields,
    IDs as IDs,
    close as close,
    configure as configure,
    i_ds_from as i_ds_from,
    is_configured as is_configured,
    record as record,
    with_i_ds as with_i_ds,
)

__all__ = [
    "Fields",
    "IDs",
    "close",
    "configure",
    "i_ds_from",
    "is_configured",
    "record",
    "with_i_ds",
]
