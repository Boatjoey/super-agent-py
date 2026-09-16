"""Workspace access policy and the session filesystem adapter.

Ports ``workspace`` (``context.go`` and ``workspace.go``). :class:`Context` is
the process-independent source of truth for roots and cwd; :class:`Workspace` is
the switchable binding the session and the built-in tools share.
"""

from __future__ import annotations

from super_agent.workspace.context import (
    Access as Access,
    AccessRead as AccessRead,
    AccessReadWrite as AccessReadWrite,
    Context as Context,
    NewContext as NewContext,
    NewDefaultContext as NewDefaultContext,
    Root as Root,
    canonicalDirectory as canonicalDirectory,
    canonicalNearest as canonicalNearest,
)
from super_agent.workspace.workspace import (
    New as New,
    Workspace as Workspace,
    buildContext as buildContext,
    capture as capture,
    contextFromSpec as contextFromSpec,
    detectContentType as detectContentType,
    fromSessionAccess as fromSessionAccess,
    samePath as samePath,
    specFromContext as specFromContext,
    toSessionAccess as toSessionAccess,
)

__all__ = [
    "Access",
    "AccessRead",
    "AccessReadWrite",
    "Context",
    "New",
    "NewContext",
    "NewDefaultContext",
    "Root",
    "Workspace",
    "buildContext",
    "canonicalDirectory",
    "canonicalNearest",
    "capture",
    "contextFromSpec",
    "detectContentType",
    "fromSessionAccess",
    "samePath",
    "specFromContext",
    "toSessionAccess",
]
