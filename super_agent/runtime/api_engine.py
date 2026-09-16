"""Facade re-exports for ``runtime/engine``.

The engine's compatibility surface, so callers can write ``runtime.NewEngine(...)``
without importing the subpackage. R4 in ``tests/architecture/test_dependencies.py``
keeps this file from reaching any concrete adapter.
"""

from __future__ import annotations

from super_agent.runtime.engine import (
    COMPACT_SUMMARY_PROMPT as COMPACT_SUMMARY_PROMPT,
    Engine as Engine,
    EngineView as EngineView,
    NewEngine as NewEngine,
    NewEngineWithComponents as NewEngineWithComponents,
    NewEngineWithExecutor as NewEngineWithExecutor,
    NewEngineWithExecutorAndPolicy as NewEngineWithExecutorAndPolicy,
    PolicySetter as PolicySetter,
    PolicySnapshot as PolicySnapshot,
    PolicyStore as PolicyStore,
    StateObserver as StateObserver,
    cloneToolBatch as cloneToolBatch,
    errorString as errorString,
    estimateMessageTokens as estimateMessageTokens,
    estimateTokens as estimateTokens,
    millis_since as millis_since,
    typeName as typeName,
)

__all__ = [
    "COMPACT_SUMMARY_PROMPT",
    "Engine",
    "EngineView",
    "NewEngine",
    "NewEngineWithComponents",
    "NewEngineWithExecutor",
    "NewEngineWithExecutorAndPolicy",
    "PolicySetter",
    "PolicySnapshot",
    "PolicyStore",
    "StateObserver",
    "cloneToolBatch",
    "errorString",
    "estimateMessageTokens",
    "estimateTokens",
    "millis_since",
    "typeName",
]
