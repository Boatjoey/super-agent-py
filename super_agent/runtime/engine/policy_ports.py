"""The optional policy capabilities the engine probes for.

These three protocols cannot live in ``engine.py``: it imports the mixins that
need to check them, so the protocols need a home that imports nothing from the
engine.

``runtime/engine/__init__.py`` and ``engine.py`` both re-export them, so
``engine.PolicySetter`` is reachable from either.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from super_agent.runtime.execution import PermissionMode, PermissionRules, Policy


@runtime_checkable
class PolicySetter(Protocol):
    """A resolver that can be handed a new policy mid-session."""

    def set_policy(self, policy: Policy) -> None: ...


@runtime_checkable
class PolicyStore(Protocol):
    """An approval store that remembers which policy is in force."""

    def set_permission_policy(self, mode: PermissionMode, rules: PermissionRules) -> None: ...


@runtime_checkable
class PolicySnapshot(Protocol):
    """A policy that can report the mode and rules it was built from.

    The engine seeds its approval store from this when it constructs a resolver
    with a caller-supplied policy, so the two copies of the policy cannot start
    out disagreeing.
    """

    def mode(self) -> PermissionMode: ...

    def rules(self) -> PermissionRules: ...
