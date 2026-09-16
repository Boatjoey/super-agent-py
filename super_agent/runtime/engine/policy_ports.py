"""The optional policy capabilities the engine probes for.

Go declares these three interfaces in ``engine.go`` because an interface can be
declared anywhere without import consequences. Python cannot: ``engine.py``
imports the mixins that need to check these protocols, so the protocols need a
home that imports nothing from the engine.

``runtime/engine/__init__.py`` and ``engine.py`` both re-export them, so
``engine.PolicySetter`` reads exactly as it does in Go.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from super_agent.runtime.execution import PermissionMode, PermissionRules, Policy


@runtime_checkable
class PolicySetter(Protocol):
    """A resolver that can be handed a new policy mid-session."""

    def SetPolicy(self, policy: Policy) -> None: ...


@runtime_checkable
class PolicyStore(Protocol):
    """An approval store that remembers which policy is in force."""

    def SetPermissionPolicy(self, mode: PermissionMode, rules: PermissionRules) -> None: ...


@runtime_checkable
class PolicySnapshot(Protocol):
    """A policy that can report the mode and rules it was built from.

    The engine seeds its approval store from this when it constructs a resolver
    with a caller-supplied policy, so the two copies of the policy cannot start
    out disagreeing.
    """

    def Mode(self) -> PermissionMode: ...

    def Rules(self) -> PermissionRules: ...
