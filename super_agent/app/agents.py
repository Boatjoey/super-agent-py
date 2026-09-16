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
from super_agent.app.instructions import Bundle, Load as loadInstructions
from super_agent.app.system_prompt import SystemPrompt
from super_agent.app.workflows import WorkflowController
from super_agent.runtime import (
    Message,
    PermissionMode,
    PermissionModePlan,
    RoleSystem,
    Session,
    ToolCall,
    ToolSpec,
    ValidPermissionMode,
)
from super_agent.runtime.execution import ZeroPermissionMode
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

    Name: str = ""
    Provider: str = ""
    Model: str = ""
    Prompt: str = ""
    PermissionMode: PermissionMode = ZeroPermissionMode
    Tools: tuple[str, ...] = ()


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

    def Specs(self) -> list[ToolSpec]:
        specs = self._runner.Specs()
        if self._allowed is None:
            return specs
        return [spec for spec in specs if spec.Name in self._allowed]

    async def Run(self, ctx: RunContext, call: ToolCall) -> str:
        if self._allowed is not None and call.Name not in self._allowed:
            raise ValueError("tool is not enabled for active agent: " + call.Name)
        return await self._runner.Run(ctx, call)

    def setAllowed(self, names: Sequence[str]) -> None:
        """Restrict the visible tools, or lift the restriction for an empty list."""
        self._allowed = None if not names else frozenset(names)


class routedModel:
    """The model the engine calls, which :meth:`AgentController.Use` swaps.

    The engine holds this one object for its whole life, so a profile switch
    reaches it without rewiring the executor.
    """

    __slots__ = ("_model",)

    def __init__(self, model: Model) -> None:
        self._model = model

    async def Next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        return await self._model.Next(ctx, messages, tools, on_stream_chunk)

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

    async def GitDiff(self, ctx: RunContext) -> str:
        return await self.workflows.GitDiff(ctx)

    async def GitStatus(self, ctx: RunContext) -> str:
        return await self.workflows.GitStatus(ctx)

    async def Diagnostics(self, ctx: RunContext, path: str) -> str:
        return await self.workflows.Diagnostics(ctx, path)

    async def RunHook(self, ctx: RunContext, event: str) -> None:
        await self.workflows.RunHook(ctx, event)

    async def RunHooks(self, ctx: RunContext, *events: str) -> None:
        await self.workflows.RunHooks(ctx, *events)

    def CustomCommands(self) -> list[str]:
        return self.workflows.CustomCommands()

    def Skills(self) -> list[str]:
        return self.workflows.Skills()

    def Plugins(self) -> list[str]:
        return self.workflows.Plugins()

    def ExpandCommand(self, name: str, arguments: str) -> str:
        return self.workflows.ExpandCommand(name, arguments)

    # --- the profile table ---------------------------------------------------

    def List(self) -> list[AgentProfile]:
        """Every profile, ordered by name."""
        return sorted(self.profiles.values(), key=lambda profile: profile.Name)

    def Current(self) -> AgentProfile:
        """The active profile, or a zero one when the name is unknown."""
        return self.profiles.get(self.current, AgentProfile())

    async def Use(self, name: str) -> None:
        """Switch to ``name``, replacing the context, model, and tool set.

        The conversation is replaced before the permission mode changes, so the
        new mode is never in force over the old conversation.
        """
        profile = self.profiles.get(name)
        if profile is None:
            raise ValueError("unknown agent: " + name)
        config = self.providers[profile.Provider]
        if profile.Model != "":
            config = dataclasses.replace(config, Model=profile.Model)
        model = llm.NewModel(profile.Provider, config)
        messages, _bundle = initialMessagesWithAgent(self.base, profile)
        memories = self.session.Memories()
        if memories:
            messages = [
                *messages,
                Message(Role=RoleSystem, Content="Cross-session memory:\n- " + "\n- ".join(memories)),
            ]
        await self.session.ReplaceConversation(messages)
        await self.session.SetPermissionMode(profile.PermissionMode)
        self.model.set(model)
        if self.tools is not None:
            self.tools.setAllowed(profile.Tools)
        self.current = name


def buildAgentProfiles(cfg: Config, providers: dict[str, llm.ProviderConfig]) -> dict[str, AgentProfile]:
    """Build ``build``, ``plan``, and every configured custom profile.

    ``build`` and ``plan`` are reserved: a custom profile that takes either name
    would be silently shadowed otherwise, so it is an error instead.
    """
    skillPrompt = ""
    if cfg.Extensions.SkillPrompt != "":
        skillPrompt = "\n\nAvailable skills:\n" + cfg.Extensions.SkillPrompt
    profiles: dict[str, AgentProfile] = {
        "build": AgentProfile(
            Name="build",
            Provider=cfg.Provider,
            Model=cfg.ModelConfig.Model,
            Prompt="Build mode: inspect, implement, verify, and finish requested changes." + skillPrompt,
            PermissionMode=cfg.PermissionMode,
        ),
        "plan": AgentProfile(
            Name="plan",
            Provider=cfg.Provider,
            Model=cfg.ModelConfig.Model,
            Prompt="Plan mode: investigate and propose a plan. Do not modify files or run mutating tools."
            + skillPrompt,
            PermissionMode=PermissionModePlan,
        ),
    }
    for rawName, item in cfg.Agents.items():
        name = rawName.strip()
        if name == "" or name in ("build", "plan"):
            raise ValueError("custom agent name is empty or reserved: " + name)
        provider = firstNonEmpty(item.Provider, cfg.Provider)
        providerCfg = providers.get(provider)
        if providerCfg is None:
            raise ValueError("agent " + name + " references unknown provider: " + provider)
        if item.Model != "":
            providerCfg = dataclasses.replace(providerCfg, Model=item.Model)
        mode = PermissionMode(firstNonEmpty(item.PermissionMode, str(cfg.PermissionMode)))
        if not ValidPermissionMode(mode):
            raise ValueError("agent " + name + " has invalid permission mode: " + str(mode))
        profiles[name] = AgentProfile(
            Name=name,
            Provider=provider,
            Model=providerCfg.Model,
            Prompt=item.Prompt.strip() + skillPrompt,
            PermissionMode=mode,
            Tools=tuple(item.Tools),
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
    content = SystemPrompt
    if bundle.Content != "":
        content += "\n\n" + bundle.Content
    return [Message(Role=RoleSystem, Content=content)], bundle


def initialMessagesWithAgent(cwd: str, profile: AgentProfile) -> tuple[list[Message], Bundle]:
    """:func:`initialMessages` with the active profile's prompt appended.

    The message value is frozen, so a replacement is built rather than mutated in
    place.
    """
    messages, bundle = initialMessages(cwd)
    if profile.Prompt != "":
        first = messages[0]
        messages[0] = dataclasses.replace(
            first,
            Content=first.Content + "\n\nActive agent profile: " + profile.Name + "\n" + profile.Prompt,
        )
    return messages, bundle
