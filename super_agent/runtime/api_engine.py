"""Facade re-exports for ``runtime/engine``.

The engine's compatibility surface, so callers can write ``runtime.new_engine(...)``
without importing the subpackage. R4 in ``tests/architecture/test_dependencies.py``
keeps this file from reaching any concrete adapter.
"""

from __future__ import annotations

from super_agent.runtime.engine import (
    COMPACT_SUMMARY_PROMPT as COMPACT_SUMMARY_PROMPT,
    Engine as Engine,
    EngineView as EngineView,
    PolicySetter as PolicySetter,
    PolicySnapshot as PolicySnapshot,
    PolicyStore as PolicyStore,
    StateObserver as StateObserver,
    cloneToolBatch as cloneToolBatch,
    errorString as errorString,
    estimateMessageTokens as estimateMessageTokens,
    estimateTokens as estimateTokens,
    millis_since as millis_since,
    new_engine as new_engine,
    new_engine_with_components as new_engine_with_components,
    new_engine_with_executor as new_engine_with_executor,
    new_engine_with_executor_and_policy as new_engine_with_executor_and_policy,
    typeName as typeName,
)

__all__ = [
    "COMPACT_SUMMARY_PROMPT",
    "Engine",
    "EngineView",
    "PolicySetter",
    "PolicySnapshot",
    "PolicyStore",
    "StateObserver",
    "cloneToolBatch",
    "errorString",
    "estimateMessageTokens",
    "estimateTokens",
    "millis_since",
    "new_engine",
    "new_engine_with_components",
    "new_engine_with_executor",
    "new_engine_with_executor_and_policy",
    "typeName",
]
