"""The ``delegate`` tool: running a task in a child agent.

A delegation creates a real persistent child session with its own workspace, its
own tool registry, and its own engine, runs one turn, and returns the child's
final assistant message to the parent call.

Three rules are kept exactly:

* the recursion depth is carried on the run context, and a fourth level is
  refused;
* a delegated worktree is created and removed through the *sandboxed* command
  tool, under ``<workspace>/.super-agent/worktrees/<nano>-<seq>`` — the hyphenated
  spelling, which is what distinguishes it from the configuration directories;
* a sub-session auto-denies every approval request, so a delegation can never
  block on a prompt nobody is watching.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import itertools
import json
import os
import shutil
import time
from collections.abc import Iterator, Sequence
from typing import Final

from super_agent import jsonutil, llm, tools
from super_agent.app.agents import AgentProfile, filteredToolRunner, initialMessagesWithAgent
from super_agent.app.config import firstNonEmpty, instructionSourcePaths
from super_agent.jsonutil import json_field
from super_agent.runtime import (
    DENY_APPROVAL,
    ROLE_ASSISTANT,
    ROLE_SYSTEM,
    ApprovalDecision,
    DefaultScheduledActionExecutor,
    Message,
    Metadata,
    PermissionRules,
    Session,
    ToolCall,
    ToolSpec,
    create_persistent_session,
    new_engine_with_executor_and_policy,
    new_policy,
)
from super_agent.runtime.protocol.run_context import RunContext, live_context
from super_agent.runtime.protocol.types import Model
from super_agent.runtime.session import (
    ApprovalsClosed,
    NotificationsClosed,
    SessionNotification,
    ToolApprovalRequested,
)
from super_agent.store import Repository
from super_agent.tools.sandbox import SandboxConfig
from super_agent.workspace import Workspace, new as newWorkspace, new_default_context

__all__ = [
    "finalAssistantContent",
    "maxSubagentDepth",
    "subagentDepthKey",
    "subagentInput",
    "subagentTool",
    "truncate",
]

#: How many levels of delegation are allowed. The first child runs at depth 1.
maxSubagentDepth: Final[int] = 3

#: The run-context key carrying the current depth. An object identity is the
#: private-type equivalent.
subagentDepthKey: Final[object] = object()

#: How long a worktree removal may take before the directory is deleted instead.
WORKTREE_TIMEOUT_SECONDS: Final[float] = 60.0


@dataclasses.dataclass(frozen=True, slots=True)
class subagentInput:
    """The ``delegate`` tool's arguments."""

    prompt: str = dataclasses.field(default="", metadata=json_field(name="prompt"))
    agent: str = dataclasses.field(default="", metadata=json_field(name="agent"))
    worktree: bool = dataclasses.field(default=False, metadata=json_field(name="worktree"))


@dataclasses.dataclass(slots=True)
class subagentTool:
    """One level of delegation, bound to the parent session."""

    parent: Session
    repository: Repository
    profiles: dict[str, AgentProfile]
    providers: dict[str, llm.ProviderConfig]
    sandbox: SandboxConfig
    rules: PermissionRules
    base: str
    workspace: Workspace | None
    sequence: Iterator[int] = dataclasses.field(default_factory=lambda: itertools.count(1))

    def specs(self) -> list[ToolSpec]:
        return [self.spec()]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="delegate",
            description="Run a task in a child agent and return its final result.",
            risky=True,
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "agent": {"type": "string"},
                    "worktree": {"type": "boolean"},
                },
                "required": ["prompt"],
            },
        )

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        """Run one delegation and return the child's final result."""
        rawDepth = ctx.value(subagentDepthKey)
        depth = rawDepth if isinstance(rawDepth, int) else 0
        if depth >= maxSubagentDepth:
            raise ValueError("maximum subagent depth reached")
        try:
            input = jsonutil.loads(call.input, subagentInput)
        except (ValueError, TypeError) as error:
            raise ValueError(f"decode delegate input: {error}") from error
        prompt = input.prompt.strip()
        if prompt == "":
            raise ValueError("delegate prompt is required")
        name = firstNonEmpty(input.agent.strip(), "build")
        profile = self.profiles.get(name)
        if profile is None:
            raise ValueError("unknown subagent profile: " + name)
        modelConfig = self.providers[profile.provider]
        if profile.model != "":
            modelConfig = dataclasses.replace(modelConfig, model=profile.model)
        model = llm.new_model(profile.provider, modelConfig)
        cwd = self.base
        created = False
        if input.worktree:
            cwd = await self.createWorktree(ctx)
            created = True
        try:
            return await self.runChild(ctx, profile, prompt, cwd, model, depth)
        finally:
            # Every exit path tears the worktree down, so delegations do not
            # accumulate worktree directories and git worktree metadata.
            if created:
                await self.removeWorktree(cwd)

    async def runChild(
        self, ctx: RunContext, profile: AgentProfile, prompt: str, cwd: str, model: Model, depth: int
    ) -> str:
        """Build the child session, run one turn, and return its final result."""
        initial, bundle = initialMessagesWithAgent(cwd, profile)
        memories = self.repository.load_memory()
        if memories:
            initial = [
                *initial,
                Message(role=ROLE_SYSTEM, content="Cross-session memory:\n- " + "\n- ".join(memories)),
            ]
        sandbox = dataclasses.replace(self.sandbox, workspace=cwd)
        childWorkspace = new_default_context(cwd)
        childWorkspaceRuntime = newWorkspace(childWorkspace)
        registry = tools.sandboxed_registry(sandbox, childWorkspaceRuntime)
        childDelegate = dataclasses.replace(self, base=cwd, workspace=childWorkspaceRuntime)
        registry.add(childDelegate)
        filtered = filteredToolRunner(registry)
        filtered.setAllowed(profile.tools)
        engine = new_engine_with_executor_and_policy(
            DefaultScheduledActionExecutor(model, filtered),
            new_policy(profile.permission_mode, self.rules),
            initial,
        )
        await engine.ready()
        parentMeta = self.parent.metadata()
        child = create_persistent_session(
            engine,
            self.repository,
            childWorkspaceRuntime,
            Metadata(
                parent_id=parentMeta.id,
                provider=profile.provider,
                model=profile.model,
                cwd=cwd,
                title="subagent: " + truncate(prompt, 48),
                instruction_sources=instructionSourcePaths(bundle),
                project_id=parentMeta.project_id,
                config_root=cwd,
            ),
            initial,
        )
        result = ""
        try:
            await self.runTurn(ctx, child, prompt, depth)
            result = finalAssistantContent(child.snapshot().messages)
        finally:
            with contextlib.suppress(Exception):
                await child.close()
        if result == "":
            raise ValueError("subagent returned no assistant result")
        return f"Child session: {child.metadata().id}\nWorkspace: {cwd}\n\n{result}"

    async def runTurn(self, ctx: RunContext, child: Session, prompt: str, depth: int) -> None:
        """Run one child turn, denying every approval it asks for.

        The denial runs on its own task while the turn blocks, so the same
        decoupling holds; the task is cancelled once the turn ends so nothing
        outlives the delegation.
        """
        notifications: asyncio.Queue[SessionNotification] = asyncio.Queue(maxsize=100)
        approvals: asyncio.Queue[ApprovalDecision | ApprovalsClosed] = asyncio.Queue(maxsize=1)

        async def denyApprovals() -> None:
            while True:
                notification = await notifications.get()
                if isinstance(notification, NotificationsClosed):
                    return
                if isinstance(notification, ToolApprovalRequested):
                    await approvals.put(DENY_APPROVAL)

        denier = asyncio.create_task(denyApprovals())
        try:
            await child.run_turn(ctx.with_value(subagentDepthKey, depth + 1), prompt, notifications, approvals)
        finally:
            denier.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await denier

    async def createWorktree(self, ctx: RunContext) -> str:
        """Create a detached worktree through the sandboxed ``run_command`` tool."""
        if self.workspace is None:
            raise ValueError("workspace context is required")
        identifier = f"{time.time_ns()}-{next(self.sequence)}"
        path = self.workspace.resolve_path(os.path.join(".super-agent", "worktrees", identifier))
        if not self.workspace.can_write(path):
            raise ValueError("worktree path is outside writable workspace roots")
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        command = "git worktree add --detach " + json.dumps(path) + " HEAD"
        payload = jsonutil.dumps({"command": command, "cwd": self.base, "timeout_seconds": 60})
        registry = tools.sandboxed_registry(self.sandbox, self.workspace)
        try:
            await registry.run(ctx, ToolCall(name="run_command", input=payload))
        except Exception as error:
            raise ValueError(f"create worktree: {error}") from error
        return path

    async def removeWorktree(self, path: str) -> None:
        """Tear down a delegation worktree on a fresh context.

        The delegation context may already be cancelled when the cleanup runs, so
        the removal gets a context of its own and a deadline. A refusal falls back
        to deleting the tree, so the workspace does not accumulate orphans.
        """
        command = "git worktree remove --force " + json.dumps(path)
        payload = jsonutil.dumps({"command": command, "cwd": self.base, "timeout_seconds": 60})
        if self.workspace is None:
            shutil.rmtree(path, ignore_errors=True)
            return
        try:
            registry = tools.sandboxed_registry(self.sandbox, self.workspace)
            await asyncio.wait_for(
                registry.run(live_context(), ToolCall(name="run_command", input=payload)),
                timeout=WORKTREE_TIMEOUT_SECONDS,
            )
        except Exception:
            shutil.rmtree(path, ignore_errors=True)


def finalAssistantContent(messages: Sequence[Message]) -> str:
    """The newest assistant message's text, or the empty string."""
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].role == ROLE_ASSISTANT:
            return messages[index].content.strip()
    return ""


def truncate(value: str, limit: int) -> str:
    """``value`` cut to ``limit`` characters, with an ellipsis when it was cut."""
    if len(value) <= limit:
        return value
    return value[:limit] + "\u2026"
