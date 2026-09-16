"""The complete mutable machine data.

``RuntimeData`` is replaced, never mutated in place: a transition works on a
clone, validates it, and only then does the engine commit it. The type itself is
therefore mutable — :meth:`~super_agent.runtime.machine.runtime_data_change_applier.clone_runtime_data`
is what keeps that mutation off the committed value.
"""

from __future__ import annotations

import dataclasses

from super_agent.runtime.machine.state import State
from super_agent.runtime.machine.tool_batch import ToolCallBatch
from super_agent.runtime.permission.types import Request as PermissionRequest
from super_agent.runtime.protocol.types import Message, ToolCall


@dataclasses.dataclass(slots=True)
class RuntimeData:
    state: State
    messages: list[Message] = dataclasses.field(default_factory=list[Message])
    pending_tool: ToolCall | None = None  # awaiting approval
    pending_permission: PermissionRequest | None = None
    current_tool: ToolCall | None = None  # executing
    tool_batch: ToolCallBatch | None = None  # remaining queue
    streaming_content: str = ""
    streaming_reasoning: str = ""
