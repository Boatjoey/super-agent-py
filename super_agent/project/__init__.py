"""Project root resolution.

Kept separate from ``workspace``: the project is a logical identity, the
workspace is the set of directories the agent may reach.
"""

from __future__ import annotations

from super_agent.project.project import (
    Project as Project,
    Resolve as Resolve,
    canonicalDirectory as canonicalDirectory,
)

__all__ = [
    "Project",
    "Resolve",
    "canonicalDirectory",
]
