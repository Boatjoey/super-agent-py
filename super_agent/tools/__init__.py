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
    DefaultRegistry as DefaultRegistry,
    NewRegistry as NewRegistry,
    Registry as Registry,
    RegistryForWorkspace as RegistryForWorkspace,
    SandboxedRegistry as SandboxedRegistry,
    Tool as Tool,
)
from super_agent.tools.sandbox import (
    DefaultSandboxConfig as DefaultSandboxConfig,
    SandboxConfig as SandboxConfig,
    SandboxMode as SandboxMode,
    SandboxModeOff as SandboxModeOff,
    SandboxModeStrict as SandboxModeStrict,
    ValidSandboxMode as ValidSandboxMode,
)
from super_agent.tools.web import (
    BrowserFetchTool as BrowserFetchTool,
    WebSearchTool as WebSearchTool,
)
from super_agent.tools.workspace import WorkspaceContext as WorkspaceContext
