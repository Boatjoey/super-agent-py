"""The engine: the only place the state machine, the loop, and the ports meet.

The engine owns four things the rest of the runtime must not duplicate: the state
lock, the action queue, the run lifecycle, and the discard rule for stale action
results. Everything else — policy, storage, presentation — is a port or an adapter
around it.

The class is assembled from three mixins because Python cannot split one class
across modules, and the split is worth keeping: ``commands.py`` has the
commands, ``action_loop.py`` the loop, ``query.py`` the reads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from super_agent.runtime.engine.action_loop import ActionLoopMixin
from super_agent.runtime.engine.commands import CommandsMixin
from super_agent.runtime.engine.policy_ports import (
    PolicySetter as PolicySetter,
    PolicySnapshot as PolicySnapshot,
    PolicyStore as PolicyStore,
)
from super_agent.runtime.engine.query import QueryMixin
from super_agent.runtime.execution import (
    ActionQueue,
    ActionResultResolver,
    ApprovalStore,
    DefaultActionResultResolver,
    DefaultRunController,
    DefaultScheduledActionExecutor,
    DefaultScheduledActionRunner,
    MemoryApprovalStore,
    Policy,
    RunController,
    ScheduledActionExecutor,
    ScheduledActionRunner,
    new_default_policy,
)
from super_agent.runtime.machine import (
    STATE_INITIALIZING,
    DefaultRuntimeDataChangeApplier,
    Message,
    RuntimeData,
    RuntimeDataChangeApplier,
)
from super_agent.runtime.protocol.types import Model, ToolRunner

#: A per-turn observer. It runs outside the engine lock so it can read snapshots.
StateObserver = Callable[[], Awaitable[None]]


class Engine(CommandsMixin, ActionLoopMixin, QueryMixin):
    """Drives the state machine for one session."""

    def __init__(
        self,
        runner: ScheduledActionRunner,
        resolver: ActionResultResolver,
        runtimeDataChangeApplier: RuntimeDataChangeApplier,
        runs: RunController,
        approvals: ApprovalStore,
        initial: list[Message] | None,
    ) -> None:
        #: Held only across synchronous critical sections. Public so a test can
        #: prove the state observer never runs while it is held.
        self.lock: asyncio.Lock = asyncio.Lock()
        self._runner = runner
        self._resolver = resolver
        self._applier = runtimeDataChangeApplier
        self._runs = runs
        self._approvals = approvals
        self._runtime_data = RuntimeData(state=STATE_INITIALIZING, messages=list(initial or []))
        self._action_queue = ActionQueue()
        self._state_observer: StateObserver | None = None

    def set_state_observer(self, observer: StateObserver | None) -> None:
        """Install the per-turn observer, or clear it with ``None``."""
        self._state_observer = observer

    async def _notify_state_observer(self) -> None:
        """Fire the observer, never while holding the lock.

        Assignment and read of the observer field are atomic, and nothing awaits
        between them. Awaiting the observer while holding the lock would instead
        let it deadlock against its own reads.
        """
        observer = self._state_observer
        if observer is not None:
            await observer()


def new_engine(model: Model | None, tools: ToolRunner | None, initial: list[Message] | None) -> Engine:
    """The ordinary wiring: real executor, default policy, default everything."""
    return new_engine_with_executor(DefaultScheduledActionExecutor(model, tools), initial)


def new_engine_with_executor(executor: ScheduledActionExecutor, initial: list[Message] | None) -> Engine:
    """Wire the default policy and remember its mode and rules for approvals."""
    approvals = MemoryApprovalStore()
    policy = new_default_policy()
    approvals.set_permission_policy(policy.mode(), policy.rules())
    return new_engine_with_components(
        DefaultScheduledActionRunner(executor),
        DefaultActionResultResolver(policy, approvals),
        DefaultRuntimeDataChangeApplier(),
        DefaultRunController(),
        approvals,
        initial,
    )


def new_engine_with_executor_and_policy(
    executor: ScheduledActionExecutor, policy: Policy, initial: list[Message] | None
) -> Engine:
    """As :func:`NewEngineWithExecutor`, with the caller's policy.

    The approval store is seeded from the policy's own mode and rules when it
    exposes them, so the engine's two copies of the policy cannot start out
    disagreeing.
    """
    approvals = MemoryApprovalStore()
    if isinstance(policy, PolicySnapshot):
        approvals.set_permission_policy(policy.mode(), policy.rules())
    return new_engine_with_components(
        DefaultScheduledActionRunner(executor),
        DefaultActionResultResolver(policy, approvals),
        DefaultRuntimeDataChangeApplier(),
        DefaultRunController(),
        approvals,
        initial,
    )


def new_engine_with_components(
    runner: ScheduledActionRunner,
    resolver: ActionResultResolver,
    runtimeDataChangeApplier: RuntimeDataChangeApplier,
    runs: RunController,
    approvals: ApprovalStore,
    initial: list[Message] | None,
) -> Engine:
    """Assemble the engine from its ports; the constructor tests substitute through."""
    return Engine(runner, resolver, runtimeDataChangeApplier, runs, approvals, initial)


__all__ = [
    "Engine",
    "PolicySetter",
    "PolicySnapshot",
    "PolicyStore",
    "StateObserver",
    "new_engine",
    "new_engine_with_components",
    "new_engine_with_executor",
    "new_engine_with_executor_and_policy",
]
