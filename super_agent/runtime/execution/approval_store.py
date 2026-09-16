"""Remembered "always allow" decisions.

A decision is keyed by the tool name and a hash of the *canonical* input, so
``{"a":1,"b":2}`` and ``{"b":2,  "a":1}`` are the same call. The store is in
memory only: it lives as long as the process, and nothing about it is persisted.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

from super_agent.runtime.execution.policy import PermissionMode, PermissionRules
from super_agent.runtime.protocol.types import ToolCall


@dataclasses.dataclass(frozen=True, slots=True)
class ApprovalKey:
    """What "always allow" remembers: which tool, called how."""

    ToolName: str = ""
    InputHash: str = ""


class ApprovalStore(Protocol):
    """Reads and writes the always-allow set."""

    def AllowAlways(self, key: ApprovalKey) -> None: ...

    def IsAlwaysAllowed(self, key: ApprovalKey) -> bool: ...


class MemoryApprovalStore:
    """The only store; the port exists so tests can substitute one.

    No lock is needed the way Go needs a ``sync.Mutex``: every caller runs on the
    same event loop and neither method awaits, so two calls cannot interleave.
    """

    __slots__ = ("_always", "_mode", "_rules")

    def __init__(self) -> None:
        self._always: dict[ApprovalKey, bool] = {}
        self._mode = PermissionMode("")
        self._rules = PermissionRules()

    def AllowAlways(self, key: ApprovalKey) -> None:
        self._always[key] = True

    def IsAlwaysAllowed(self, key: ApprovalKey) -> bool:
        return self._always.get(key, False)

    def SetPermissionPolicy(self, mode: PermissionMode, rules: PermissionRules) -> None:
        self._mode = mode
        self._rules = rules

    def PermissionMode(self) -> PermissionMode:
        return self._mode

    def PermissionRules(self) -> PermissionRules:
        return self._rules


def NewApprovalKey(call: ToolCall) -> ApprovalKey:
    """The key an approval decision is recorded under."""
    return ApprovalKey(ToolName=call.Name, InputHash=hashCanonicalInput(call.Input))


def hashCanonicalInput(input: str) -> str:
    """SHA-256 of the input in its canonical JSON form.

    Input that is not JSON is hashed as written, matching Go: ``json.Unmarshal``
    fails and the raw text is used.

    The canonical form reproduces Go's ``json.Marshal`` for the JSON data model:
    sorted object keys, compact separators, raw UTF-8, HTML-escaped ``<``, ``>``,
    ``&``, and the two line separators, and integral floats written without a
    fractional part. Two Go quirks are not reproduced and cannot matter here
    because the value never leaves memory: floats in the ``1e-6``..``1e-7`` band
    (Go switches to exponent form exactly there) and the control characters that
    Go escapes as ``\\u0008``/``\\u000c`` where Python uses ``\\b``/``\\f``.
    """
    try:
        parsed: Any = json.loads(input)
    except (TypeError, ValueError):
        return hashlib.sha256(input.encode()).hexdigest()
    return hashlib.sha256(_canonical_json(parsed).encode()).hexdigest()


def _canonical_json(value: Any) -> str:
    text = json.dumps(
        _normalise_numbers(value),
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    )
    # Safe on the whole document: in JSON output these characters can only occur
    # inside a string literal, never as punctuation.
    return (
        text.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _normalise_numbers(value: Any) -> Any:
    """Write an integral float the way Go does: ``1`` rather than ``1.0``."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_normalise_numbers(item) for item in cast("Sequence[Any]", value)]
    if isinstance(value, dict):
        return {key: _normalise_numbers(item) for key, item in cast("Mapping[str, Any]", value).items()}
    return value
