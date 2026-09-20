"""Startup configuration: settings, flags, environment, and the selected project.

Configuration comes from exactly three places — ``~/.superagent/settings.json``,
the environment, and the command line — and the result is one :class:`Config`
value the rest of the composition root reads. There is no project-level settings
file and no layered merge.

Two spellings of the configuration directory are in play and neither may be
unified: ``~/.superagent/`` (settings, home, no hyphen) and ``<workspace>/.super-
agent/`` (worktrees and exports, with a hyphen). This module only ever uses the
first.

:func:`load_config` resolves the selected provider's credential, and that resolved
value is what the session builds the adapter from — the raw ``providers`` map on
:class:`Config` is kept only so a custom agent profile can name a different
provider. Building the model from the unresolved map is a credential bug: it can
send the template placeholder ``sk-...`` as a real bearer token.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from typing import Any, Final, cast

from super_agent import jsonutil, project
from super_agent.app.extensions import Extensions, ExtensionSettings, loadExtensions
from super_agent.app.instructions import Bundle, load as loadInstructions
from super_agent.jsonutil import json_field
from super_agent.llm import ProviderConfig
from super_agent.runtime import (
    PERMISSION_MODE_BYPASS,
    PermissionMode,
    PermissionRules,
    valid_permission_mode,
)
from super_agent.runtime.execution import ZERO_PERMISSION_MODE
from super_agent.tools.lsp import ServerConfig as LSPServerConfig
from super_agent.tools.mcp import ServerConfig as MCPServerConfig
from super_agent.tools.sandbox import SandboxConfig, SandboxMode, valid_sandbox_mode
from super_agent.tui.statusline import DEFAULT_ORDER, ITEMS
from super_agent.workspace import Context, new_default_context

__all__ = [
    "AgentSettings",
    "Config",
    "Flags",
    "LSPServerSettings",
    "Lookup",
    "MCPServerSettings",
    "PermissionSettings",
    "SandboxSettings",
    "Settings",
    "TUISettings",
    "TelemetrySettings",
    "apikeyPlaceholder",
    "decodeExtensions",
    "default_settings",
    "envTrue",
    "firstNonEmpty",
    "load_config",
    "load_settings",
    "load_settings_file",
    "normalizeSettings",
    "resolveProviderConfig",
    "resolve_status_line",
    "save_settings_file",
    "settings_path",
]

#: Where an unset variable is looked up. ``None`` means "not set".
type Lookup = Callable[[str], str | None]

#: The user-level configuration directory spelling: no hyphen, under home.
USER_CONFIG_DIRECTORY: Final[str] = ".superagent"

#: The placeholder :func:`default_settings` writes into a fresh ``settings.json``.
#: It is not a credential, so it is treated as unset.
CLAUDE_PLACEHOLDER: Final[str] = "sk-ant-..."
DEFAULT_PLACEHOLDER: Final[str] = "sk-..."


@dataclasses.dataclass(frozen=True, slots=True)
class Flags:
    """The command-line switches, as parsed by :mod:`super_agent.cli`."""

    auto_approve_tools: bool = False
    no_tools: bool = False
    permission_mode: str = ""
    cwd: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class TelemetrySettings:
    """The ``telemetry`` block of ``settings.json``."""

    log_path: str = dataclasses.field(default="", metadata=json_field(name="log_path"))


@dataclasses.dataclass(frozen=True, slots=True)
class AgentSettings:
    """One custom agent profile from ``agents``."""

    provider: str = dataclasses.field(default="", metadata=json_field(name="provider", omitempty=True))
    model: str = dataclasses.field(default="", metadata=json_field(name="model", omitempty=True))
    prompt: str = dataclasses.field(default="", metadata=json_field(name="prompt", omitempty=True))
    permission_mode: str = dataclasses.field(default="", metadata=json_field(name="permission_mode", omitempty=True))
    tools: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="tools", omitempty=True))


@dataclasses.dataclass(frozen=True, slots=True)
class LSPServerSettings:
    """One entry of ``lsp_servers``."""

    command: str = dataclasses.field(default="", metadata=json_field(name="command"))
    args: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="args"))
    extensions: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="extensions"))
    language_id: str = dataclasses.field(default="", metadata=json_field(name="language_id"))


@dataclasses.dataclass(frozen=True, slots=True)
class MCPServerSettings:
    """One entry of ``mcp_servers``."""

    command: str = dataclasses.field(default="", metadata=json_field(name="command"))
    args: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="args"))
    env: dict[str, str] = dataclasses.field(default_factory=dict[str, str], metadata=json_field(name="env"))
    cwd: str = dataclasses.field(default="", metadata=json_field(name="cwd"))
    connect_timeout_seconds: int = dataclasses.field(default=0, metadata=json_field(name="connect_timeout_seconds"))
    call_timeout_seconds: int = dataclasses.field(default=0, metadata=json_field(name="call_timeout_seconds"))


@dataclasses.dataclass(frozen=True, slots=True)
class SandboxSettings:
    """The ``sandbox`` block of ``settings.json``."""

    mode: str = dataclasses.field(default="", metadata=json_field(name="mode"))
    cpu_seconds: int = dataclasses.field(default=0, metadata=json_field(name="cpu_seconds"))
    memory_mb: int = dataclasses.field(default=0, metadata=json_field(name="memory_mb"))
    max_processes: int = dataclasses.field(default=0, metadata=json_field(name="max_processes"))
    max_open_files: int = dataclasses.field(default=0, metadata=json_field(name="max_open_files"))


@dataclasses.dataclass(frozen=True, slots=True)
class PermissionSettings:
    """The ``permissions`` block of ``settings.json``."""

    mode: str = dataclasses.field(default="", metadata=json_field(name="mode"))
    allow_tools: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_tools"))
    deny_tools: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_tools"))
    allow_command_prefixes: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="allow_command_prefixes")
    )
    deny_command_prefixes: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="deny_command_prefixes")
    )
    allow_paths: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_paths"))
    deny_paths: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_paths"))
    allow_env: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_env"))
    deny_env: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_env"))
    network: str = dataclasses.field(default="", metadata=json_field(name="network"))


@dataclasses.dataclass(frozen=True, slots=True)
class TUISettings:
    """The ``tui`` block of ``settings.json``.

    ``status_line`` defaults to the order ``docs/config.md`` documents rather
    than to ``None``, and that is what keeps an absent key and an explicit
    ``null`` two different values: absent draws the default row, ``null`` removes
    it. A list draws exactly the items it names.
    """

    status_line: tuple[str, ...] | None = DEFAULT_ORDER


@dataclasses.dataclass(frozen=True, slots=True)
class Settings:
    """``~/.superagent/settings.json``, field for field.

    Every field is written, because none of them is ``omitempty``: a template file
    a user opens has to show every knob it can turn.
    """

    provider: str = dataclasses.field(default="", metadata=json_field(name="provider"))
    providers: dict[str, ProviderConfig] = dataclasses.field(
        default_factory=dict[str, ProviderConfig], metadata=json_field(name="providers")
    )
    permissions: PermissionSettings = dataclasses.field(
        default_factory=PermissionSettings, metadata=json_field(name="permissions")
    )
    sandbox: SandboxSettings = dataclasses.field(default_factory=SandboxSettings, metadata=json_field(name="sandbox"))
    mcp_servers: dict[str, MCPServerSettings] = dataclasses.field(
        default_factory=dict[str, MCPServerSettings], metadata=json_field(name="mcp_servers")
    )
    lsp_servers: dict[str, LSPServerSettings] = dataclasses.field(
        default_factory=dict[str, LSPServerSettings], metadata=json_field(name="lsp_servers")
    )
    agent: str = dataclasses.field(default="", metadata=json_field(name="agent"))
    agents: dict[str, AgentSettings] = dataclasses.field(
        default_factory=dict[str, AgentSettings], metadata=json_field(name="agents")
    )
    extensions: ExtensionSettings = dataclasses.field(
        default_factory=ExtensionSettings, metadata=json_field(name="extensions")
    )
    telemetry: TelemetrySettings = dataclasses.field(
        default_factory=TelemetrySettings, metadata=json_field(name="telemetry")
    )
    tui: TUISettings = dataclasses.field(default_factory=TUISettings, metadata=json_field(name="tui"))


@dataclasses.dataclass(slots=True)
class Config:
    """Everything startup decided, resolved and ready for the session.

    Mutable on purpose: :func:`super_agent.app.session.new_session_with_extensions`
    points ``sandbox.workspace`` at the workspace's primary root once it knows it.
    """

    provider: str = ""
    no_tools: bool = False
    permission_mode: PermissionMode = ZERO_PERMISSION_MODE
    permission_rules: PermissionRules = dataclasses.field(default_factory=PermissionRules)
    sandbox: SandboxConfig = dataclasses.field(default_factory=SandboxConfig)
    mcp_servers: list[MCPServerConfig] = dataclasses.field(default_factory=list[MCPServerConfig])
    lsp_servers: list[LSPServerConfig] = dataclasses.field(default_factory=list[LSPServerConfig])
    #: The selected provider's configuration, credential included. This is the
    #: value the session builds the model from.
    model_config: ProviderConfig = dataclasses.field(default_factory=ProviderConfig)
    provider_configs: dict[str, ProviderConfig] = dataclasses.field(default_factory=dict[str, ProviderConfig])
    instructions: Bundle = dataclasses.field(default_factory=Bundle)
    instruction_sources: tuple[str, ...] = ()
    agents: dict[str, AgentSettings] = dataclasses.field(default_factory=dict[str, AgentSettings])
    agent: str = ""
    extensions: Extensions = dataclasses.field(default_factory=Extensions)
    telemetry_path: str = ""
    #: The validated ``tui.status_line``: the documented default order when the
    #: setting is absent, and the empty tuple when it is ``null``, which removes
    #: the row. Both are values, so the two cases stay different here too.
    status_line: tuple[str, ...] = DEFAULT_ORDER
    project: project.Project = dataclasses.field(default_factory=project.Project)
    workspace: Context | None = None
    config_root: str = ""


def default_settings() -> Settings:
    """The template written on first run, and the source of every default."""
    return Settings(
        provider="deepseek",
        providers={
            "deepseek": ProviderConfig(
                base_url="https://api.deepseek.com",
                api_key=DEFAULT_PLACEHOLDER,
                model="deepseek-reasoner",
            ),
            "openai": ProviderConfig(api_key=DEFAULT_PLACEHOLDER, model="gpt-4o"),
            "claude": ProviderConfig(api_key=CLAUDE_PLACEHOLDER, model="claude-3-7-sonnet-20250219"),
        },
        permissions=PermissionSettings(mode="ask", network="deny"),
        sandbox=SandboxSettings(mode="strict", cpu_seconds=120, memory_mb=1024, max_processes=128, max_open_files=256),
        mcp_servers={},
        lsp_servers={},
        agent="build",
        agents={},
        extensions=ExtensionSettings(commands={}, hooks={}),
    )


def load_config(flags: Flags, lookup: Lookup | None = None) -> Config:
    """Combine flags, environment, and settings into one :class:`Config`.

    Every failure here is fatal on purpose: an unknown permission mode, a sandbox
    mode this build cannot honour, or a provider with no usable credential fails
    at startup with a message that names what is missing, rather than surfacing as
    an authentication error on the first turn.
    """
    if lookup is None:
        lookup = os.environ.get
    settings = load_settings()
    provider = settings.provider
    processCWD = os.getcwd()
    selectedProject = project.resolve(flags.cwd, processCWD)
    workspaceContext = new_default_context(selectedProject.root)
    cwd = workspaceContext.get_cwd()
    telemetryPath = settings.telemetry.log_path
    if telemetryPath == "":
        telemetryPath = os.path.join(os.path.expanduser("~"), USER_CONFIG_DIRECTORY, "telemetry.jsonl")
    elif not os.path.isabs(telemetryPath):
        telemetryPath = os.path.join(cwd, telemetryPath)
    bundle = loadInstructions(cwd)
    extensions = loadExtensions(settings.extensions, cwd)
    mode = PermissionMode(firstNonEmpty(flags.permission_mode, settings.permissions.mode, "ask"))
    # The YOLO environment variable is a fallback for when no explicit mode was
    # requested; an explicit --approval-mode flag always wins so a checked-in
    # .env cannot silently disable permission prompts.
    if flags.auto_approve_tools and flags.permission_mode != "":
        raise ValueError(
            "--yolo and --approval-mode are mutually exclusive; use --approval-mode bypass instead of --yolo"
        )
    if flags.auto_approve_tools or (flags.permission_mode == "" and envTrue(lookup, "YOLO")):
        mode = PERMISSION_MODE_BYPASS
    if not valid_permission_mode(mode):
        raise ValueError("invalid permission mode: " + str(mode))
    sandboxMode = SandboxMode(settings.sandbox.mode)
    if not valid_sandbox_mode(sandboxMode):
        raise ValueError("invalid sandbox mode: " + settings.sandbox.mode)
    status_line = resolve_status_line(settings.tui.status_line)
    rules = PermissionRules(
        allow_tools=settings.permissions.allow_tools,
        deny_tools=settings.permissions.deny_tools,
        allow_prefixes=settings.permissions.allow_command_prefixes,
        deny_prefixes=settings.permissions.deny_command_prefixes,
        allow_paths=settings.permissions.allow_paths,
        deny_paths=settings.permissions.deny_paths,
        allow_env=settings.permissions.allow_env,
        deny_env=settings.permissions.deny_env,
        network=firstNonEmpty(settings.permissions.network, "deny"),
    )
    # Settings come from JSON, where object key order is not meaningful, so the
    # server names are sorted to make the resolved order deterministic.
    mcpServers: list[MCPServerConfig] = []
    for name in sorted(settings.mcp_servers):
        server = settings.mcp_servers[name]
        serverCWD = server.cwd
        if serverCWD == "":
            serverCWD = cwd
        elif not os.path.isabs(serverCWD):
            serverCWD = os.path.join(cwd, serverCWD)
        mcpServers.append(
            MCPServerConfig(
                name=name,
                command=server.command,
                args=list(server.args),
                env=dict(server.env),
                cwd=serverCWD,
                connect_timeout=float(server.connect_timeout_seconds),
                call_timeout=float(server.call_timeout_seconds),
            )
        )
    lspServers: list[LSPServerConfig] = [
        LSPServerConfig(
            name=name,
            command=server.command,
            args=tuple(server.args),
            extensions=tuple(server.extensions),
            language_id=server.language_id,
            root=cwd,
        )
        for name, server in sorted(settings.lsp_servers.items())
    ]
    providerConfig = resolveProviderConfig(settings, provider, lookup)
    return Config(
        provider=provider,
        no_tools=flags.no_tools or envTrue(lookup, "NO_TOOLS"),
        permission_mode=mode,
        permission_rules=rules,
        sandbox=SandboxConfig(
            mode=sandboxMode,
            workspace=cwd,
            allow_network=rules.network == "allow",
            cpu_seconds=settings.sandbox.cpu_seconds,
            memory_bytes=settings.sandbox.memory_mb << 20,
            max_processes=settings.sandbox.max_processes,
            max_open_files=settings.sandbox.max_open_files,
        ),
        mcp_servers=mcpServers,
        lsp_servers=lspServers,
        model_config=providerConfig,
        provider_configs=settings.providers,
        instructions=bundle,
        instruction_sources=instructionSourcePaths(bundle),
        agents=settings.agents,
        agent=firstNonEmpty(settings.agent, "build"),
        extensions=extensions,
        telemetry_path=telemetryPath,
        status_line=status_line,
        project=selectedProject,
        workspace=workspaceContext,
        config_root=selectedProject.root,
    )


def apikeyPlaceholder(provider: str) -> str:
    """The value :func:`default_settings` writes for ``provider``.

    It is not a real credential, so it is treated as "unset" to let the provider's
    environment variable take over instead of being sent as a bearer token.
    """
    if provider == "claude":
        return CLAUDE_PLACEHOLDER
    return DEFAULT_PLACEHOLDER


def decodeExtensions(content: str) -> ExtensionSettings:
    """The ``extensions`` block of a settings file, decoded by the shared codec.

    The keys are the ones the file uses (``commands``, ``hooks``, ``skills``,
    ``plugins``); :class:`ExtensionSettings` declares them, so this is just the
    ordinary JSON path and nothing sees a differently-spelled key.
    """
    data: Any = json.loads(content)
    if not isinstance(data, Mapping):
        return ExtensionSettings()
    block = cast("Mapping[str, Any]", data).get("extensions")
    if not isinstance(block, Mapping):
        return ExtensionSettings()
    return jsonutil.from_json_value(cast("Mapping[str, Any]", block), ExtensionSettings)


def resolveProviderConfig(settings: Settings, provider: str, lookup: Lookup) -> ProviderConfig:
    """Resolve the credential for ``provider``, or raise naming what is missing."""
    config = settings.providers.get(provider)
    if config is None:
        raise ValueError("provider " + provider + " is not configured: add it to the providers map in settings.json")
    if config.api_key == apikeyPlaceholder(provider):
        config = dataclasses.replace(config, api_key="")
    if config.api_key == "":
        envKey = provider.upper() + "_API_KEY"
        value = lookup(envKey)
        if value:
            return dataclasses.replace(config, api_key=value)
        raise ValueError("provider " + provider + " has no api_key: set it in settings.json or export " + envKey)
    return config


def resolve_status_line(items: tuple[str, ...] | None) -> tuple[str, ...]:
    """The validated ``tui.status_line``, with ``null`` resolved to "no row".

    An item outside the vocabulary fails the load here rather than being dropped
    when the row is drawn, because a setting that silently does nothing is worse
    than one the user is told about. ``null`` arrives as ``None`` and becomes the
    empty tuple — a row with no items — which is a value and not the absence of
    the key; an absent key draws the default order.
    """
    if items is None:
        return ()
    for item in items:
        if item not in ITEMS:
            raise ValueError("invalid status line item: " + item)
    return tuple(items)


def envTrue(lookup: Lookup, key: str) -> bool:
    """Whether ``key`` is set to the exact string ``true``.

    ``True``, ``1``, and ``yes`` do not count, which is what keeps a switch that
    changes the permission model from being enabled by accident.
    """
    return lookup(key) == "true"


def firstNonEmpty(*values: str) -> str:
    """The first value that is not the empty string."""
    for value in values:
        if value != "":
            return value
    return ""


def instructionSourcePaths(bundle: Bundle) -> tuple[str, ...]:
    """The paths a bundle was assembled from, in the order it read them."""
    return tuple(source.path for source in bundle.sources)


def load_settings() -> Settings:
    """Read ``~/.superagent/settings.json``, creating the template if it is absent."""
    return load_settings_file(settings_path())


def settings_path() -> str:
    """``~/.superagent/settings.json`` — home, and spelled without a hyphen."""
    return os.path.join(os.path.expanduser("~"), USER_CONFIG_DIRECTORY, "settings.json")


def load_settings_file(path: str) -> Settings:
    """Read one settings file, or write the template when there is none.

    An existing file is never overwritten: a parse failure is reported rather than
    repaired, because silently replacing a file the user edited would throw away
    the edit and the error that explains it.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except FileNotFoundError:
        settings = default_settings()
        # The same atomic write a save performs: a half-written settings.json
        # would make every later startup fail to parse it.
        save_settings_file(path, settings)
        return settings
    settings = jsonutil.loads(content, Settings)
    return normalizeSettings(dataclasses.replace(settings, extensions=decodeExtensions(content)))


def save_settings_file(path: str, settings: Settings) -> None:
    """Write ``settings.json`` atomically, mode ``0600``, two-space indented.

    Same-directory temp file, chmod, write, fsync, close, then ``os.replace``: the
    rename is the only step that is visible, so a crash mid-write cannot leave a
    truncated file behind.
    """
    content = (jsonutil.dumps(settings, indent=2) + "\n").encode("utf-8")
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".settings-", suffix=".json")
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        # The rename is the only step that removes the temp file; anything else
        # leaves it behind, and a failed remove must not mask the real failure.
        with contextlib.suppress(OSError):
            os.remove(temporary)
        raise


def normalizeSettings(settings: Settings) -> Settings:
    """Fill in what a stored file may leave out, and never what it states."""
    defaults = default_settings().sandbox
    permissions = settings.permissions or PermissionSettings()
    sandbox = settings.sandbox or SandboxSettings()
    return dataclasses.replace(
        settings,
        providers=settings.providers or {},
        agents=settings.agents or {},
        lsp_servers=settings.lsp_servers or {},
        mcp_servers=settings.mcp_servers or {},
        extensions=settings.extensions or ExtensionSettings(),
        telemetry=settings.telemetry or TelemetrySettings(),
        tui=settings.tui or TUISettings(),
        agent=settings.agent or "build",
        permissions=dataclasses.replace(
            permissions,
            mode=permissions.mode or "ask",
            network=permissions.network or "deny",
        ),
        sandbox=dataclasses.replace(
            sandbox,
            mode=sandbox.mode or defaults.mode,
            cpu_seconds=sandbox.cpu_seconds if sandbox.cpu_seconds > 0 else defaults.cpu_seconds,
            memory_mb=sandbox.memory_mb if sandbox.memory_mb > 0 else defaults.memory_mb,
            max_processes=sandbox.max_processes if sandbox.max_processes > 0 else defaults.max_processes,
            max_open_files=sandbox.max_open_files if sandbox.max_open_files > 0 else defaults.max_open_files,
        ),
    )


def settingsMap(configs: list[MCPServerConfig]) -> dict[str, MCPServerSettings]:
    """The settings-shaped view of the configured MCP servers."""
    result: dict[str, MCPServerSettings] = {}
    for config in configs:
        result[config.name] = MCPServerSettings(
            command=config.command,
            args=tuple(config.args),
            env=dict(config.env),
            cwd=config.cwd,
            connect_timeout_seconds=int(config.connect_timeout),
            call_timeout_seconds=int(config.call_timeout),
        )
    return result
