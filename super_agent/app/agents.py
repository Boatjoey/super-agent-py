"""Agent profiles, the switchable model, and the tool filter.

A profile is a named bundle of provider, model, prompt, permission mode, and tool
allow-list; switching profiles replaces the conversation's system context, the
model, the permission mode, and the visible tools together, so the four can never
disagree about which agent is active.

Two design choices are worth naming:

* every method that touches the session is a coroutine, because the session's use
  cases are;
* the mutable state needs no lock: every mutation here runs on the single event
  loop and none of them yields between reading and writing, so a lock would only
  be ceremony.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Sequence

from super_agent import llm
from super_agent.app.config import Config, firstNonEmpty
from super_agent.app.instructions import Bundle, load as loadInstructions
from super_agent.app.system_prompt import SYSTEM_PROMPT
from super_agent.app.workflows import WorkflowController
from super_agent.runtime import (
    PERMISSION_MODE_PLAN,
    ROLE_SYSTEM,
    Message,
    PermissionMode,
    Session,
    ToolCall,
    ToolSpec,
    valid_permission_mode,
)
from super_agent.runtime.execution import ZERO_PERMISSION_MODE
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Model, ModelResponse, StreamChunk, ToolRunner

__all__ = [
    "AgentController",
    "AgentProfile",
    "buildAgentProfiles",
    "filteredToolRunner",
    "initialMessages",
    "initialMessagesWithAgent",
    "routedModel",
]


@dataclasses.dataclass(frozen=True, slots=True)
class AgentProfile:
    """One selectable agent: who it talks to, as what, and with which tools."""

    name: str = ""
    provider: str = ""
    model: str = ""
    prompt: str = ""
    permission_mode: PermissionMode = ZERO_PERMISSION_MODE
    tools: tuple[str, ...] = ()


class filteredToolRunner:
    """A runner that advertises and accepts only the active profile's tools.

    ``allowed`` is ``None`` when nothing is restricting the tool set. A profile
    that names no tools leaves it ``None`` too, rather than restricting to the
    empty set: "run with the default tools" is the absence of a restriction, not a
    list of zero tools.
    """

    __slots__ = ("_allowed", "_runner")

    def __init__(self, runner: ToolRunner) -> None:
        self._runner = runner
        self._allowed: frozenset[str] | None = None

    def specs(self) -> list[ToolSpec]:
        specs = self._runner.specs()
        if self._allowed is None:
            return specs
        return [spec for spec in specs if spec.name in self._allowed]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        if self._allowed is not None and call.name not in self._allowed:
            raise ValueError("tool is not enabled for active agent: " + call.name)
        return await self._runner.run(ctx, call)

    def setAllowed(self, names: Sequence[str]) -> None:
        """Restrict the visible tools, or lift the restriction for an empty list."""
        self._allowed = None if not names else frozenset(names)


class routedModel:
    """The model the engine calls, which :meth:`AgentController.use` swaps.

    The engine holds this one object for its whole life, so a profile switch
    reaches it without rewiring the executor.
    """

    __slots__ = ("_model",)

    def __init__(self, model: Model) -> None:
        self._model = model

    async def next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        return await self._model.next(ctx, messages, tools, on_stream_chunk)

    def set(self, model: Model) -> None:
        """Point the router at a different provider."""
        self._model = model


class AgentController:
    """Lists, switches, and describes the configured agent profiles."""

    __slots__ = ("base", "current", "model", "profiles", "providers", "session", "tools", "workflows")

    def __init__(
        self,
        *,
        session: Session,
        model: routedModel,
        profiles: dict[str, AgentProfile],
        providers: dict[str, llm.ProviderConfig],
        workflows: WorkflowController,
        tools: filteredToolRunner | None,
        base: str,
        current: str,
    ) -> None:
        self.session = session
        self.model = model
        self.profiles = profiles
        self.providers = providers
        self.workflows = workflows
        self.tools = tools
        self.base = base
        self.current = current

    # --- workflow delegation -------------------------------------------------

    async def git_diff(self, ctx: RunContext) -> str:
        return await self.workflows.git_diff(ctx)

    async def git_status(self, ctx: RunContext) -> str:
        return await self.workflows.git_status(ctx)

    async def diagnostics(self, ctx: RunContext, path: str) -> str:
        return await self.workflows.diagnostics(ctx, path)

    async def run_hook(self, ctx: RunContext, event: str) -> None:
        await self.workflows.run_hook(ctx, event)

    async def run_hooks(self, ctx: RunContext, *events: str) -> None:
        await self.workflows.run_hooks(ctx, *events)

    def custom_commands(self) -> list[str]:
        return self.workflows.custom_commands()

    def skills(self) -> list[str]:
        return self.workflows.skills()

    def plugins(self) -> list[str]:
        return self.workflows.plugins()

    def expand_command(self, name: str, arguments: str) -> str:
        return self.workflows.expand_command(name, arguments)

    # --- the profile table ---------------------------------------------------

    def list(self) -> list[AgentProfile]:
        """Every profile, ordered by name."""
        return sorted(self.profiles.values(), key=lambda profile: profile.name)

    def active_profile(self) -> AgentProfile:
        """The active profile, or a zero one when the name is unknown."""
        return self.profiles.get(self.current, AgentProfile())

    async def use(self, name: str) -> None:
        """Switch to ``name``, replacing the context, model, and tool set.

        The conversation is replaced before the permission mode changes, so the
        new mode is never in force over the old conversation.
        """
        profile = self.profiles.get(name)
        if profile is None:
            raise ValueError("unknown agent: " + name)
        config = self.providers[profile.provider]
        if profile.model != "":
            config = dataclasses.replace(config, model=profile.model)
        model = llm.new_model(profile.provider, config)
        messages, _bundle = initialMessagesWithAgent(self.base, profile)
        memories = self.session.memories()
        if memories:
            messages = [
                *messages,
                Message(role=ROLE_SYSTEM, content="Cross-session memory:\n- " + "\n- ".join(memories)),
            ]
        await self.session.replace_conversation(messages)
        await self.session.set_permission_mode(profile.permission_mode)
        self.model.set(model)
        if self.tools is not None:
            self.tools.setAllowed(profile.tools)
        self.current = name


def buildAgentProfiles(cfg: Config, providers: dict[str, llm.ProviderConfig]) -> dict[str, AgentProfile]:
    """Build ``build``, ``plan``, and every configured custom profile.

    ``build`` and ``plan`` are reserved: a custom profile that takes either name
    would be silently shadowed otherwise, so it is an error instead.
    """
    skillPrompt = ""
    if cfg.extensions.skill_prompt != "":
        skillPrompt = "\n\nAvailable skills:\n" + cfg.extensions.skill_prompt
    profiles: dict[str, AgentProfile] = {
        "build": AgentProfile(
            name="build",
            provider=cfg.provider,
            model=cfg.model_config.model,
            prompt="Build mode: inspect, implement, verify, and finish requested changes." + skillPrompt,
            permission_mode=cfg.permission_mode,
        ),
        "plan": AgentProfile(
            name="plan",
            provider=cfg.provider,
            model=cfg.model_config.model,
            prompt="Plan mode: investigate and propose a plan. Do not modify files or run mutating tools."
            + skillPrompt,
            permission_mode=PERMISSION_MODE_PLAN,
        ),
    }
    for rawName, item in cfg.agents.items():
        name = rawName.strip()
        if name == "" or name in ("build", "plan"):
            raise ValueError("custom agent name is empty or reserved: " + name)
        provider = firstNonEmpty(item.provider, cfg.provider)
        providerCfg = providers.get(provider)
        if providerCfg is None:
            raise ValueError("agent " + name + " references unknown provider: " + provider)
        if item.model != "":
            providerCfg = dataclasses.replace(providerCfg, model=item.model)
        mode = PermissionMode(firstNonEmpty(item.permission_mode, str(cfg.permission_mode)))
        if not valid_permission_mode(mode):
            raise ValueError("agent " + name + " has invalid permission mode: " + str(mode))
        profiles[name] = AgentProfile(
            name=name,
            provider=provider,
            model=providerCfg.model,
            prompt=item.prompt.strip() + skillPrompt,
            permission_mode=mode,
            tools=tuple(item.tools),
        )
    return profiles


def initialMessages(cwd: str) -> tuple[list[Message], Bundle]:
    """The built-in prompt plus every instruction layer that applies to ``cwd``.

    The bundle is returned alongside so session metadata can record where the
    instructions came from. It lives here rather than in the session module
    because :func:`initialMessagesWithAgent` is its only caller and the session
    module would otherwise need a mutual import.
    """
    bundle = loadInstructions(cwd)
    content = SYSTEM_PROMPT
    if bundle.content != "":
        content += "\n\n" + bundle.content
    return [Message(role=ROLE_SYSTEM, content=content)], bundle


def initialMessagesWithAgent(cwd: str, profile: AgentProfile) -> tuple[list[Message], Bundle]:
    """:func:`initialMessages` with the active profile's prompt appended.

    The message value is frozen, so a replacement is built rather than mutated in
    place.
    """
    messages, bundle = initialMessages(cwd)
    if profile.prompt != "":
        first = messages[0]
        messages[0] = dataclasses.replace(
            first,
            content=first.content + "\n\nActive agent profile: " + profile.name + "\n" + profile.prompt,
        )
    return messages, bundle
