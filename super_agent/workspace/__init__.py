"""Workspace access policy and the session filesystem adapter.

:class:`Context` is the process-independent source of truth for roots and cwd;
:class:`Workspace` is the switchable binding the session and the built-in tools
share.
"""

from __future__ import annotations

from super_agent.workspace.context import (
    ACCESS_READ as ACCESS_READ,
    ACCESS_READ_WRITE as ACCESS_READ_WRITE,
    Access as Access,
    Context as Context,
    Root as Root,
    canonicalDirectory as canonicalDirectory,
    canonicalNearest as canonicalNearest,
    new_context as new_context,
    new_default_context as new_default_context,
)
from super_agent.workspace.workspace import (
    Workspace as Workspace,
    buildContext as buildContext,
    capture as capture,
    contextFromSpec as contextFromSpec,
    detectContentType as detectContentType,
    fromSessionAccess as fromSessionAccess,
    new as new,
    samePath as samePath,
    specFromContext as specFromContext,
    toSessionAccess as toSessionAccess,
)

__all__ = [
    "ACCESS_READ",
    "ACCESS_READ_WRITE",
    "Access",
    "Context",
    "Root",
    "Workspace",
    "buildContext",
    "canonicalDirectory",
    "canonicalNearest",
    "capture",
    "contextFromSpec",
    "detectContentType",
    "fromSessionAccess",
    "new",
    "new_context",
    "new_default_context",
    "samePath",
    "specFromContext",
    "toSessionAccess",
]
