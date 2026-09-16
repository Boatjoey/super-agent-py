"""The stdio language-server adapter.

The lsp package surface: everything callers write as ``lsp.X`` is re-exported
here.

``lsp.Tools()`` returns one :class:`Tool` per query name; the tools join
``tools.Registry`` like any built-in, and the manager reconnects lazily when the
workspace working directory changes.
"""

from __future__ import annotations

from super_agent.tools.lsp.client import (
    Connect as Connect,
    Manager as Manager,
    ServerConfig as ServerConfig,
    Tool as Tool,
)
