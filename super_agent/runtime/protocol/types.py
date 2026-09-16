"""Model and tool adapter contracts.

These are the types that cross the adapter boundary. Nothing here knows about the
state machine, and nothing here knows about a provider: an adapter translates its
SDK's values into these and back.

Field names keep their exported spelling so the code reads alike across the
runtime, and each field's JSON key is declared explicitly with
:func:`~super_agent.jsonutil.json_field` because the artefacts on disk have a
fixed schema. Sequence fields on these frozen types are tuples: they are values,
never mutated, and a tuple keeps the type hashable.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any, Final, Protocol

from super_agent.jsonutil import json_field
from super_agent.runtime.protocol.run_context import RunContext


class Role(str):
    """What a message is for, as a string-backed type."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Role({str.__repr__(self)})"


ROLE_SYSTEM: Final[Role] = Role("system")
ROLE_USER: Final[Role] = Role("user")
ROLE_ASSISTANT: Final[Role] = Role("assistant")
ROLE_TOOL: Final[Role] = Role("tool")

#: The zero value for the type, kept so a zero :class:`Message` is constructible
#: without a role. A message with no role is never produced by a working adapter;
#: it exists so default construction stays valid.
ZERO_ROLE: Final[Role] = Role("")


@dataclasses.dataclass(frozen=True, slots=True)
class Attachment:
    """A file handed to the model alongside a message."""

    name: str = dataclasses.field(default="", metadata=json_field(name="name"))
    mime: str = dataclasses.field(default="", metadata=json_field(name="mime"))
    data: str = dataclasses.field(default="", metadata=json_field(name="data"))


@dataclasses.dataclass(frozen=True, slots=True)
class ToolCall:
    """One tool invocation requested by the model.

    ``Input`` is the raw JSON string the model produced, not a parsed object:
    the model can emit malformed JSON and the tool decides what to do about it.
    """

    id: str = dataclasses.field(default="", metadata=json_field(name="id"))
    name: str = dataclasses.field(default="", metadata=json_field(name="name"))
    input: str = dataclasses.field(default="", metadata=json_field(name="input"))


@dataclasses.dataclass(frozen=True, slots=True)
class Message:
    """One turn of the conversation sent to a model or written to disk."""

    role: Role = dataclasses.field(default=ZERO_ROLE, metadata=json_field(name="role"))
    content: str = dataclasses.field(default="", metadata=json_field(name="content", omitempty=True))
    reasoning_content: str = dataclasses.field(
        default="", metadata=json_field(name="reasoning_content", omitempty=True)
    )
    tool_call_id: str = dataclasses.field(default="", metadata=json_field(name="tool_call_id", omitempty=True))
    tool_name: str = dataclasses.field(default="", metadata=json_field(name="tool_name", omitempty=True))
    tool_calls: tuple[ToolCall, ...] | None = dataclasses.field(
        default=None, metadata=json_field(name="tool_calls", omitempty=True)
    )
    interrupted: bool = dataclasses.field(default=False, metadata=json_field(name="interrupted", omitempty=True))
    attachments: tuple[Attachment, ...] = dataclasses.field(
        default=(), metadata=json_field(name="attachments", omitempty=True)
    )


@dataclasses.dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool as advertised to the model."""

    name: str = dataclasses.field(default="", metadata=json_field(name="name"))
    description: str = dataclasses.field(default="", metadata=json_field(name="description", omitempty=True))
    parameters: dict[str, Any] | None = dataclasses.field(
        default=None, metadata=json_field(name="parameters", omitempty=True)
    )
    risky: bool = dataclasses.field(default=False, metadata=json_field(name="risky", omitempty=True))


@dataclasses.dataclass(frozen=True, slots=True)
class Usage:
    """Provider-reported token counts for one model response.

    A ``None`` :class:`Usage` on a :class:`ModelResponse` means the adapter could
    not obtain exact numbers and telemetry should estimate instead.
    """

    input_tokens: int = dataclasses.field(default=0, metadata=json_field(name="input_tokens"))
    output_tokens: int = dataclasses.field(default=0, metadata=json_field(name="output_tokens"))
    total_tokens: int = dataclasses.field(default=0, metadata=json_field(name="total_tokens"))


@dataclasses.dataclass(frozen=True, slots=True)
class ModelResponse:
    """What one model call produced."""

    content: str = dataclasses.field(default="", metadata=json_field(name="content", omitempty=True))
    reasoning_content: str = dataclasses.field(
        default="", metadata=json_field(name="reasoning_content", omitempty=True)
    )
    tool_calls: tuple[ToolCall, ...] = dataclasses.field(
        default=(), metadata=json_field(name="tool_calls", omitempty=True)
    )
    usage: Usage | None = dataclasses.field(default=None, metadata=json_field(name="usage", omitempty=True))


@dataclasses.dataclass(frozen=True, slots=True)
class StreamChunk:
    """One incremental piece of a streaming model response."""

    content_delta: str = dataclasses.field(default="", metadata=json_field(name="content_delta", omitempty=True))
    reasoning_content_delta: str = dataclasses.field(
        default="", metadata=json_field(name="reasoning_content_delta", omitempty=True)
    )


class Model(Protocol):
    """A chat model that can request tools."""

    async def next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        """Produce one response, reporting deltas through ``on_stream_chunk``.

        ``on_stream_chunk`` is synchronous on purpose: the engine's streaming
        commit must not yield to the event loop between reading and appending.
        """
        ...


class ToolRunner(Protocol):
    """A set of tools that can be advertised and invoked."""

    def specs(self) -> list[ToolSpec]:
        """Every tool this runner exposes."""
        ...

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        """Execute ``call`` and return the text to feed back to the model."""
        ...
