"""Orchestration: the single agent loop and the state lock around it.

Python splits a Go package across files, so this module stands in for the package
namespace. The engine is assembled from mixins because one class cannot be spread
over several modules: ``commands.py`` holds the commands, ``action_loop.py`` the
loop, ``query.py`` the reads, and ``engine.py`` the constructors and the wiring.
"""

from __future__ import annotations

from super_agent.runtime.engine.action_loop import (
    ActionLoopMixin as ActionLoopMixin,
    cloneToolBatch as cloneToolBatch,
    errorString as errorString,
    estimateMessageTokens as estimateMessageTokens,
    estimateTokens as estimateTokens,
    millis_since as millis_since,
    typeName as typeName,
)
from super_agent.runtime.engine.commands import (
    COMPACT_SUMMARY_PROMPT as COMPACT_SUMMARY_PROMPT,
    CommandsMixin as CommandsMixin,
    discard_chunk as discard_chunk,
)
from super_agent.runtime.engine.engine import (
    Engine as Engine,
    NewEngine as NewEngine,
    NewEngineWithComponents as NewEngineWithComponents,
    NewEngineWithExecutor as NewEngineWithExecutor,
    NewEngineWithExecutorAndPolicy as NewEngineWithExecutorAndPolicy,
    StateObserver as StateObserver,
)
from super_agent.runtime.engine.policy_ports import (
    PolicySetter as PolicySetter,
    PolicySnapshot as PolicySnapshot,
    PolicyStore as PolicyStore,
)
from super_agent.runtime.engine.query import (
    QueryMixin as QueryMixin,
)
from super_agent.runtime.engine.snapshot import (
    EngineView as EngineView,
)

__all__ = [
    "COMPACT_SUMMARY_PROMPT",
    "ActionLoopMixin",
    "CommandsMixin",
    "Engine",
    "EngineView",
    "NewEngine",
    "NewEngineWithComponents",
    "NewEngineWithExecutor",
    "NewEngineWithExecutorAndPolicy",
    "PolicySetter",
    "PolicySnapshot",
    "PolicyStore",
    "QueryMixin",
    "StateObserver",
    "cloneToolBatch",
    "discard_chunk",
    "errorString",
    "estimateMessageTokens",
    "estimateTokens",
    "millis_since",
    "typeName",
]
