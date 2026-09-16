"""Building one session: the composition root's constructor.

Ported from ``app/session.go``. Everything the runtime needs is created here and
handed over exactly once; after :func:`NewSessionWithExtensions` returns, the
session owns the extension and language-server processes, the telemetry sink, and
the durable store.

Three shapes differ from Go, each because Python has no goroutine and no
``io.Closer`` that can await:

* the constructor is a coroutine, since connecting MCP and LSP servers is;
* an asynchronous ``Close`` is adapted to the session's synchronous
  :class:`~super_agent.runtime.session.Closer` port by :class:`asyncCloser`, whose
  coroutine :func:`waitPendingClosers` awaits after ``session.Close()``;
* the tool hooks are a runner wrapping the registry (:class:`toolHooks`) rather
  than a registry observer, because the observer port is synchronous and a hook
  runs a command.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import os
from collections.abc import Callable, Coroutine
from typing import Any

from super_agent import llm, store, tools
from super_agent.app.agents import (
    AgentController,
    buildAgentProfiles,
    filteredToolRunner,
    initialMessagesWithAgent,
    routedModel,
)
from super_agent.app.config import (
    Config,
    SettingsPath,
    firstNonEmpty,
    instructionSourcePaths,
    settingsMap,
)
from super_agent.app.mcp import MCPController, NewMCPController
from super_agent.app.subagents import subagentTool
from super_agent.app.workflows import WorkflowController
from super_agent.errors import JoinedError
from super_agent.runtime import (
    CreatePersistentSession,
    DefaultScheduledActionExecutor,
    Message,
    Metadata,
    NewEngineWithExecutorAndPolicy,
    NewPolicy,
    RoleSystem,
    Session,
    ToolCall,
    ToolSpec,
    telemetry,
)
from super_agent.runtime.protocol.run_context import LiveContext, RunContext
from super_agent.runtime.protocol.types import ToolRunner
from super_agent.tools import lsp as lsptools, mcp as mcptools
from super_agent.workspace import Context, New as newWorkspace

__all__ = [
    "NewSession",
    "NewSessionWithExtensions",
    "NewSessionWithMCP",
    "asyncCloser",
    "closerFunc",
    "contextForConfig",
    "toolHooks",
    "waitPendingClosers",
]


async def NewSession(cfg: Config) -> Session:
    """The ordinary entry point: a session, with its MCP controller discarded."""
    session, _mcp = await NewSessionWithMCP(cfg)
    return session


async def NewSessionWithMCP(cfg: Config) -> tuple[Session, MCPController | None]:
    """A session and its MCP controller, without the agent controller."""
    session, mcp, _agents = await NewSessionWithExtensions(cfg)
    return session, mcp


async def NewSessionWithExtensions(cfg: Config) -> tuple[Session, MCPController | None, AgentController]:
    """Build the runtime, the tools, and the controllers for one session.

    The model is built from the *resolved* provider configuration — ``cfg.ModelConfig``
    — and not from the raw ``cfg.ProviderConfigs`` entry. That entry still holds
    the template placeholder whenever the credential came from the environment,
    and sending ``sk-...`` as a bearer token is the bug this port fixes.
    """
    workspaceContext = contextForConfig(cfg)
    cwd = workspaceContext.GetCWD()
    configRoot = firstNonEmpty(cfg.ConfigRoot, cfg.Project.Root, cwd)
    workspaceRuntime = newWorkspace(workspaceContext)
    cfg.Sandbox.Workspace = workspaceContext.GetPrimaryRoot()
    telemetry.Configure(cfg.TelemetryPath)
    telemetryOwned = True
    extension: mcptools.Manager | None = None
    lspCloser: lsptools.Manager | None = None
    try:
        providers = dict(cfg.ProviderConfigs)
        if not providers:
            providers = {cfg.Provider: cfg.ModelConfig}
        else:
            # The selected provider's entry is replaced by the resolved one, so a
            # profile naming the default provider gets the resolved credential and
            # a profile naming another provider still gets its own entry.
            providers[cfg.Provider] = cfg.ModelConfig
        profiles = buildAgentProfiles(cfg, providers)
        agentName = firstNonEmpty(cfg.Agent, "build")
        profile = profiles.get(agentName)
        if profile is None:
            raise ValueError("unknown configured agent: " + agentName)
        providerConfig = providers[profile.Provider]
        if profile.Model != "":
            providerConfig = dataclasses.replace(providerConfig, Model=profile.Model)
        model = llm.NewModel(profile.Provider, providerConfig)
        router = routedModel(model)
        toolRunner: ToolRunner
        registry: tools.Registry | None = None
        hooks: toolHooks | None = None
        controller: MCPController | None = None
        toolFilter: filteredToolRunner | None = None
        if cfg.NoTools:
            toolRunner = tools.NoTools()
        else:
            registry = tools.SandboxedRegistry(cfg.Sandbox, workspaceRuntime)
            manager = await mcptools.Connect(LiveContext(), cfg.MCPServers)
            try:
                registry.Add(*manager.Tools())
            except BaseException:
                with contextlib.suppress(Exception):
                    await manager.Close()
                raise
            controller = NewMCPController(manager, registry, SettingsPath(), cwd, settingsMap(cfg.MCPServers))
            # The runtime session owns extension process lifetime after creation.
            extension = manager
            if cfg.LSPServers:
                lspManager = await lsptools.Connect(LiveContext(), workspaceRuntime, cfg.LSPServers)
                try:
                    for lspTool in lspManager.Tools():
                        registry.Add(lspTool)
                except BaseException:
                    with contextlib.suppress(Exception):
                        await lspManager.Close()
                    raise
                lspCloser = lspManager
            hooks = toolHooks(registry)
            toolFilter = filteredToolRunner(hooks)
            toolFilter.setAllowed(profile.Tools)
            toolRunner = toolFilter
        initial, bundle = initialMessagesWithAgent(configRoot, profile)
        st = store.OpenDefault()
        repository = store.NewRepository(st)
        memories = repository.LoadMemory()
        if memories:
            initial = [
                *initial,
                Message(Role=RoleSystem, Content="Cross-session memory:\n- " + "\n- ".join(memories)),
            ]
        engine = NewEngineWithExecutorAndPolicy(
            DefaultScheduledActionExecutor(router, toolRunner),
            NewPolicy(profile.PermissionMode, cfg.PermissionRules),
            initial,
        )
        await engine.Ready()
        session = CreatePersistentSession(
            engine,
            repository,
            workspaceRuntime,
            Metadata(
                Provider=profile.Provider,
                Model=profile.Model,
                CWD=cwd,
                Title=os.path.basename(cwd),
                InstructionSources=instructionSourcePaths(bundle),
                ProjectID=cfg.Project.ID,
                ConfigRoot=configRoot,
            ),
            initial,
        )
        if registry is not None and hooks is not None:
            registry.SetCheckpointCallback(session.Checkpoint)
            delegate = subagentTool(
                parent=session,
                repository=repository,
                profiles=profiles,
                providers=providers,
                sandbox=cfg.Sandbox,
                rules=cfg.PermissionRules,
                base=cwd,
                workspace=workspaceRuntime,
            )
            try:
                registry.Add(delegate)
            except BaseException:
                with contextlib.suppress(Exception):
                    await session.Close()
                raise
        session.ConfigurePermissions(profile.PermissionMode, cfg.PermissionRules)
        if extension is not None:
            session.AddCloser(asyncCloser(extension.Close))
            extension = None
        if lspCloser is not None:
            session.AddCloser(asyncCloser(lspCloser.Close))
            lspCloser = None
        session.AddCloser(closerFunc(telemetry.Close))
        telemetryOwned = False
        workflows = WorkflowController(registry=hooks, extensions=cfg.Extensions)
        if hooks is not None:
            hooks.SetWorkflows(workflows)
        try:
            await workflows.RunHooks(LiveContext(), "session_start", "startup")
        except BaseException:
            with contextlib.suppress(Exception):
                await session.Close()
            raise
        agents = AgentController(
            session=session,
            model=router,
            profiles=profiles,
            providers=providers,
            workflows=workflows,
            tools=toolFilter,
            base=cwd,
            current=profile.Name,
        )
        return session, controller, agents
    except BaseException:
        # Go's deferred cleanup, which runs only while ownership was never handed
        # over. Once the session owns a closer the field it was read from is
        # cleared, so nothing is closed twice.
        if extension is not None:
            with contextlib.suppress(Exception):
                await extension.Close()
        if lspCloser is not None:
            with contextlib.suppress(Exception):
                await lspCloser.Close()
        if telemetryOwned:
            telemetry.Close()
        raise


class toolHooks:
    """Runs the ``pre_tool`` and ``post_tool`` extension hooks around a call.

    Go installs this as the registry's observer. Python's observer port is a
    synchronous callable while a hook runs a command, which is asynchronous, so
    the ordering the observer exists to guarantee is kept by wrapping the runner
    instead: the pre-hook finishes before the tool starts, a pre-hook failure
    aborts the call, and a post-hook failure is appended to the output rather than
    turning a finished call into a failed one. Hooks run through ``RunDirect``, so
    a hook cannot re-enter the hooks that invoked it.
    """

    __slots__ = ("_registry", "_workflows")

    def __init__(self, registry: tools.Registry) -> None:
        self._registry = registry
        self._workflows: WorkflowController | None = None

    def SetWorkflows(self, workflows: WorkflowController) -> None:
        """Install the controller the hooks run through.

        Set after construction because the controller reaches these hooks back for
        its own queries; the pair is a cycle Go closes with a registry observer.
        """
        self._workflows = workflows

    def Specs(self) -> list[ToolSpec]:
        return self._registry.Specs()

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        """Run ``call`` between the pre-tool and post-tool hooks."""
        workflows = self._workflows
        if workflows is not None:
            await workflows.RunHook(ctx, "pre_tool")
        result = ""
        error: BaseException | None = None
        try:
            result = await self._registry.RunDirect(ctx, call)
        except BaseException as failure:
            error = failure
        if workflows is not None:
            try:
                await workflows.RunHook(ctx, "post_tool")
            except Exception as hookError:
                if error is None:
                    result += "\n[post_tool hook failed: " + str(hookError) + "]"
        if error is not None:
            raise error
        return result

    async def RunDirect(self, ctx: RunContext, call: ToolCall) -> str:
        """Run ``call`` without hooks; hooks use this to avoid recursing."""
        return await self._registry.RunDirect(ctx, call)


def contextForConfig(cfg: Config) -> Context:
    """The workspace context, which a bare :class:`Config` may not carry."""
    if cfg.Workspace is not None:
        return cfg.Workspace
    raise ValueError("workspace context is required")


class closerFunc:
    """A plain callable adapted to the session's :class:`Closer` port.

    Go's ``closerFunc``: the telemetry sink closes synchronously and needs no
    adapter beyond this one.
    """

    __slots__ = ("_close",)

    def __init__(self, close: Callable[[], None]) -> None:
        self._close = close

    def Close(self) -> None:
        self._close()


#: The closes :class:`asyncCloser` scheduled that nothing has awaited yet.
_pendingCloses: list[asyncio.Task[None]] = []


class asyncCloser:
    """An asynchronous ``Close`` adapted to the session's synchronous port.

    Go hands the session one ``io.Closer`` per extension process. Its counterparts
    here close asynchronously — killing a child and waiting for it — while the
    session's close loop calls each closer synchronously. The coroutine is
    therefore scheduled on the running loop and kept, and
    :func:`waitPendingClosers` awaits it once ``session.Close()`` has returned.
    """

    __slots__ = ("_close",)

    def __init__(self, close: Callable[[], Coroutine[Any, Any, None]]) -> None:
        self._close = close

    def Close(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop is running, so nothing can be scheduled: run it here.
            asyncio.run(self._close())
            return
        _pendingCloses.append(loop.create_task(self._close()))


async def waitPendingClosers() -> None:
    """Await the closes :class:`asyncCloser` scheduled, joining the failures."""
    failures: list[BaseException] = []
    while _pendingCloses:
        task = _pendingCloses.pop()
        try:
            await task
        except Exception as error:
            failures.append(error)
    if failures:
        raise JoinedError(*failures)
