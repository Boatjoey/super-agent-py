"""The queued calls of the batch currently being advanced."""

from __future__ import annotations

import dataclasses

from super_agent.jsonutil import json_field
from super_agent.runtime.protocol.types import ToolCall


@dataclasses.dataclass(slots=True)
class ToolCallBatch:
    """One model response's tool calls, plus how far the queue has advanced.

    ``Index`` is always serialised, matching Go, because ``0`` is meaningful: it
    says no call has been dispatched yet.
    """

    ID: str = dataclasses.field(default="", metadata=json_field(name="id"))
    Calls: list[ToolCall] = dataclasses.field(default_factory=list[ToolCall], metadata=json_field(name="calls"))
    Index: int = dataclasses.field(default=0, metadata=json_field(name="index"))
