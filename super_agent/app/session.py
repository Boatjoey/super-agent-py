"""Building one session: the composition root's constructor.

Everything the runtime needs is created here and handed over exactly once; after
:func:`NewSessionWithExtensions` returns, the session owns the extension and
language-server processes, the telemetry sink, and the durable store.

Three shapes are worth naming:

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
    firstNonEmpty,
    instructionSourcePaths,
    settings_path,
    settingsMap,
)
from super_agent.app.mcp import MCPController, new_mcp_controller
from super_agent.app.subagents import subagentTool
from super_agent.app.workflows import WorkflowController
from super_agent.errors import JoinedError
from super_agent.runtime import (
    ROLE_SYSTEM,
    DefaultScheduledActionExecutor,
    Message,
    Metadata,
    Session,
    ToolCall,
    ToolSpec,
    create_persistent_session,
    new_engine_with_executor_and_policy,
    new_policy,
    telemetry,
)
from super_agent.runtime.protocol.run_context import RunContext, live_context
from super_agent.runtime.protocol.types import ToolRunner
from super_agent.tools import lsp as lsptools, mcp as mcptools
from super_agent.workspace import Context, new as newWorkspace

__all__ = [
    "asyncCloser",
    "closerFunc",
    "contextForConfig",
    "new_session",
    "new_session_with_extensions",
    "new_session_with_mcp",
    "toolHooks",
    "waitPendingClosers",
]


async def new_session(cfg: Config) -> Session:
    """The ordinary entry point: a session, with its MCP controller discarded."""
    session, _mcp = await new_session_with_mcp(cfg)
    return session


async def new_session_with_mcp(cfg: Config) -> tuple[Session, MCPController | None]:
    """A session and its MCP controller, without the agent controller."""
    session, mcp, _agents = await new_session_with_extensions(cfg)
    return session, mcp


async def new_session_with_extensions(cfg: Config) -> tuple[Session, MCPController | None, AgentController]:
    """Build the runtime, the tools, and the controllers for one session.

    The model is built from the *resolved* provider configuration — ``cfg.ModelConfig``
    — and not from the raw ``cfg.ProviderConfigs`` entry. That entry still holds
    the template placeholder whenever the credential came from the environment,
    and sending ``sk-...`` as a bearer token is the bug this port fixes.
    """
    workspaceContext = contextForConfig(cfg)
    cwd = workspaceContext.get_cwd()
    configRoot = firstNonEmpty(cfg.config_root, cfg.project.root, cwd)
    workspaceRuntime = newWorkspace(workspaceContext)
    cfg.sandbox.workspace = workspaceContext.get_primary_root()
    telemetry.configure(cfg.telemetry_path)
    telemetryOwned = True
    extension: mcptools.Manager | None = None
    lspCloser: lsptools.Manager | None = None
    try:
        providers = dict(cfg.provider_configs)
        if not providers:
            providers = {cfg.provider: cfg.model_config}
        else:
            # The selected provider's entry is replaced by the resolved one, so a
            # profile naming the default provider gets the resolved credential and
            # a profile naming another provider still gets its own entry.
            providers[cfg.provider] = cfg.model_config
        profiles = buildAgentProfiles(cfg, providers)
        agentName = firstNonEmpty(cfg.agent, "build")
        profile = profiles.get(agentName)
        if profile is None:
            raise ValueError("unknown configured agent: " + agentName)
        providerConfig = providers[profile.provider]
        if profile.model != "":
            providerConfig = dataclasses.replace(providerConfig, model=profile.model)
        model = llm.new_model(profile.provider, providerConfig)
        router = routedModel(model)
        toolRunner: ToolRunner
        registry: tools.Registry | None = None
        hooks: toolHooks | None = None
        controller: MCPController | None = None
        toolFilter: filteredToolRunner | None = None
        if cfg.no_tools:
            toolRunner = tools.NoTools()
        else:
            registry = tools.sandboxed_registry(cfg.sandbox, workspaceRuntime)
            manager = await mcptools.connect(live_context(), cfg.mcp_servers)
            try:
                registry.add(*manager.tools())
            except BaseException:
                with contextlib.suppress(Exception):
                    await manager.close()
                raise
            controller = new_mcp_controller(manager, registry, settings_path(), cwd, settingsMap(cfg.mcp_servers))
            # The runtime session owns extension process lifetime after creation.
            extension = manager
            if cfg.lsp_servers:
                lspManager = await lsptools.connect(live_context(), workspaceRuntime, cfg.lsp_servers)
                try:
                    for lspTool in lspManager.tools():
                        registry.add(lspTool)
                except BaseException:
                    with contextlib.suppress(Exception):
                        await lspManager.close()
                    raise
                lspCloser = lspManager
            hooks = toolHooks(registry)
            toolFilter = filteredToolRunner(hooks)
            toolFilter.setAllowed(profile.tools)
            toolRunner = toolFilter
        initial, bundle = initialMessagesWithAgent(configRoot, profile)
        st = store.open_default()
        repository = store.new_repository(st)
        memories = repository.load_memory()
        if memories:
            initial = [
                *initial,
                Message(role=ROLE_SYSTEM, content="Cross-session memory:\n- " + "\n- ".join(memories)),
            ]
        engine = new_engine_with_executor_and_policy(
            DefaultScheduledActionExecutor(router, toolRunner),
            new_policy(profile.permission_mode, cfg.permission_rules),
            initial,
        )
        await engine.ready()
        session = create_persistent_session(
            engine,
            repository,
            workspaceRuntime,
            Metadata(
                provider=profile.provider,
                model=profile.model,
                cwd=cwd,
                title=os.path.basename(cwd),
                instruction_sources=instructionSourcePaths(bundle),
                project_id=cfg.project.id,
                config_root=configRoot,
            ),
            initial,
        )
        if registry is not None and hooks is not None:
            registry.set_checkpoint_callback(session.checkpoint)
            delegate = subagentTool(
                parent=session,
                repository=repository,
                profiles=profiles,
                providers=providers,
                sandbox=cfg.sandbox,
                rules=cfg.permission_rules,
                base=cwd,
                workspace=workspaceRuntime,
            )
            try:
                registry.add(delegate)
            except BaseException:
                with contextlib.suppress(Exception):
                    await session.close()
                raise
        session.configure_permissions(profile.permission_mode, cfg.permission_rules)
        if extension is not None:
            session.add_closer(asyncCloser(extension.close))
            extension = None
        if lspCloser is not None:
            session.add_closer(asyncCloser(lspCloser.close))
            lspCloser = None
        session.add_closer(closerFunc(telemetry.close))
        telemetryOwned = False
        workflows = WorkflowController(registry=hooks, extensions=cfg.extensions)
        if hooks is not None:
            hooks.set_workflows(workflows)
        try:
            await workflows.run_hooks(live_context(), "session_start", "startup")
        except BaseException:
            with contextlib.suppress(Exception):
                await session.close()
            raise
        agents = AgentController(
            session=session,
            model=router,
            profiles=profiles,
            providers=providers,
            workflows=workflows,
            tools=toolFilter,
            base=cwd,
            current=profile.name,
        )
        return session, controller, agents
    except BaseException:
        # The cleanup path, which runs only while ownership was never handed over.
        # Once the session owns a closer the field it was read from is cleared, so
        # nothing is closed twice.
        if extension is not None:
            with contextlib.suppress(Exception):
                await extension.close()
        if lspCloser is not None:
            with contextlib.suppress(Exception):
                await lspCloser.close()
        if telemetryOwned:
            telemetry.close()
        raise


class toolHooks:
    """Runs the ``pre_tool`` and ``post_tool`` extension hooks around a call.

    The observer port is a synchronous callable while a hook runs a command, which
    is asynchronous, so the ordering the observer exists to guarantee is kept by
    wrapping the runner instead: the pre-hook finishes before the tool starts, a
    pre-hook failure
    aborts the call, and a post-hook failure is appended to the output rather than
    turning a finished call into a failed one. Hooks run through ``RunDirect``, so
    a hook cannot re-enter the hooks that invoked it.
    """

    __slots__ = ("_registry", "_workflows")

    def __init__(self, registry: tools.Registry) -> None:
        self._registry = registry
        self._workflows: WorkflowController | None = None

    def set_workflows(self, workflows: WorkflowController) -> None:
        """Install the controller the hooks run through.

        Set after construction because the controller reaches these hooks back for
        its own queries, so the pair is a cycle.
        """
        self._workflows = workflows

    def specs(self) -> list[ToolSpec]:
        return self._registry.specs()

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        """Run ``call`` between the pre-tool and post-tool hooks."""
        workflows = self._workflows
        if workflows is not None:
            await workflows.run_hook(ctx, "pre_tool")
        result = ""
        error: BaseException | None = None
        try:
            result = await self._registry.run_direct(ctx, call)
        except BaseException as failure:
            error = failure
        if workflows is not None:
            try:
                await workflows.run_hook(ctx, "post_tool")
            except Exception as hookError:
                if error is None:
                    result += "\n[post_tool hook failed: " + str(hookError) + "]"
        if error is not None:
            raise error
        return result

    async def run_direct(self, ctx: RunContext, call: ToolCall) -> str:
        """Run ``call`` without hooks; hooks use this to avoid recursing."""
        return await self._registry.run_direct(ctx, call)


def contextForConfig(cfg: Config) -> Context:
    """The workspace context, which a bare :class:`Config` may not carry."""
    if cfg.workspace is not None:
        return cfg.workspace
    raise ValueError("workspace context is required")


class closerFunc:
    """A plain callable adapted to the session's :class:`Closer` port.

    The telemetry sink closes synchronously and needs no adapter beyond this one.
    """

    __slots__ = ("_close",)

    def __init__(self, close: Callable[[], None]) -> None:
        self._close = close

    def close(self) -> None:
        self._close()


#: The closes :class:`asyncCloser` scheduled that nothing has awaited yet.
_pendingCloses: list[asyncio.Task[None]] = []


class asyncCloser:
    """An asynchronous ``Close`` adapted to the session's synchronous port.

    Each extension process hands the session a closer that wants to close
    asynchronously — killing a child and waiting for it — while the session's close
    loop calls each closer synchronously. The coroutine is therefore scheduled on
    the running loop and kept, and :func:`waitPendingClosers` awaits it once
    ``session.Close()`` has returned.
    """

    __slots__ = ("_close",)

    def __init__(self, close: Callable[[], Coroutine[Any, Any, None]]) -> None:
        self._close = close

    def close(self) -> None:
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
