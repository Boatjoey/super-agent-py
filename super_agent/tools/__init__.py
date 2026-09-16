"""The built-in tool adapters.

The tools package surface: everything callers write as ``tools.X`` is re-exported
here.

Tools are outbound adapters. They may import ``runtime/protocol`` but not the
root ``runtime`` facade — see ``docs/architecture.md``.
"""

from __future__ import annotations

from super_agent.tools.bash import BashTool as BashTool
from super_agent.tools.commands import (
    FormatTool as FormatTool,
    GitDiffTool as GitDiffTool,
    GitStatusTool as GitStatusTool,
    GoTestTool as GoTestTool,
    RunCommandTool as RunCommandTool,
)
from super_agent.tools.files import (
    ApplyPatchTool as ApplyPatchTool,
    ListFilesTool as ListFilesTool,
    ReadFileTool as ReadFileTool,
    SearchTool as SearchTool,
    WriteFileTool as WriteFileTool,
)
from super_agent.tools.no_tools import NoTools as NoTools
from super_agent.tools.registry import (
    Registry as Registry,
    Tool as Tool,
    default_registry as default_registry,
    new_registry as new_registry,
    registry_for_workspace as registry_for_workspace,
    sandboxed_registry as sandboxed_registry,
)
from super_agent.tools.sandbox import (
    SANDBOX_MODE_OFF as SANDBOX_MODE_OFF,
    SANDBOX_MODE_STRICT as SANDBOX_MODE_STRICT,
    SandboxConfig as SandboxConfig,
    SandboxMode as SandboxMode,
    default_sandbox_config as default_sandbox_config,
    valid_sandbox_mode as valid_sandbox_mode,
)
from super_agent.tools.web import (
    BrowserFetchTool as BrowserFetchTool,
    WebSearchTool as WebSearchTool,
)
from super_agent.tools.workspace import WorkspaceContext as WorkspaceContext
