"""Startup configuration: settings, flags, environment, and the selected project.

Configuration comes from exactly three places — ``~/.superagent/settings.json``,
the environment, and the command line — and the result is one :class:`Config`
value the rest of the composition root reads. There is no project-level settings
file and no layered merge.

Two spellings of the configuration directory are in play and neither may be
unified: ``~/.superagent/`` (settings, home, no hyphen) and ``<workspace>/.super-
agent/`` (worktrees and exports, with a hyphen). This module only ever uses the
first.

:func:`LoadConfig` resolves the selected provider's credential, and that resolved
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
from super_agent.app.instructions import Bundle, Load as loadInstructions
from super_agent.jsonutil import json_field
from super_agent.llm import ProviderConfig
from super_agent.runtime import (
    PermissionMode,
    PermissionModeBypass,
    PermissionRules,
    ValidPermissionMode,
)
from super_agent.runtime.execution import ZeroPermissionMode
from super_agent.tools.lsp import ServerConfig as LSPServerConfig
from super_agent.tools.mcp import ServerConfig as MCPServerConfig
from super_agent.tools.sandbox import SandboxConfig, SandboxMode, ValidSandboxMode
from super_agent.workspace import Context, NewDefaultContext

__all__ = [
    "AgentSettings",
    "Config",
    "DefaultSettings",
    "Flags",
    "LSPServerSettings",
    "LoadConfig",
    "LoadSettings",
    "LoadSettingsFile",
    "Lookup",
    "MCPServerSettings",
    "PermissionSettings",
    "SandboxSettings",
    "SaveSettingsFile",
    "Settings",
    "SettingsPath",
    "TelemetrySettings",
    "apikeyPlaceholder",
    "decodeExtensions",
    "envTrue",
    "firstNonEmpty",
    "normalizeSettings",
    "resolveProviderConfig",
]

#: Where an unset variable is looked up. ``None`` means "not set".
type Lookup = Callable[[str], str | None]

#: The user-level configuration directory spelling: no hyphen, under home.
USER_CONFIG_DIRECTORY: Final[str] = ".superagent"

#: The placeholder :func:`DefaultSettings` writes into a fresh ``settings.json``.
#: It is not a credential, so it is treated as unset.
CLAUDE_PLACEHOLDER: Final[str] = "sk-ant-..."
DEFAULT_PLACEHOLDER: Final[str] = "sk-..."


@dataclasses.dataclass(frozen=True, slots=True)
class Flags:
    """The command-line switches, as parsed by :mod:`super_agent.cli`."""

    AutoApproveTools: bool = False
    NoTools: bool = False
    PermissionMode: str = ""
    CWD: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class TelemetrySettings:
    """The ``telemetry`` block of ``settings.json``."""

    LogPath: str = dataclasses.field(default="", metadata=json_field(name="log_path"))


@dataclasses.dataclass(frozen=True, slots=True)
class AgentSettings:
    """One custom agent profile from ``agents``."""

    Provider: str = dataclasses.field(default="", metadata=json_field(name="provider", omitempty=True))
    Model: str = dataclasses.field(default="", metadata=json_field(name="model", omitempty=True))
    Prompt: str = dataclasses.field(default="", metadata=json_field(name="prompt", omitempty=True))
    PermissionMode: str = dataclasses.field(default="", metadata=json_field(name="permission_mode", omitempty=True))
    Tools: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="tools", omitempty=True))


@dataclasses.dataclass(frozen=True, slots=True)
class LSPServerSettings:
    """One entry of ``lsp_servers``."""

    Command: str = dataclasses.field(default="", metadata=json_field(name="command"))
    Args: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="args"))
    Extensions: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="extensions"))
    LanguageID: str = dataclasses.field(default="", metadata=json_field(name="language_id"))


@dataclasses.dataclass(frozen=True, slots=True)
class MCPServerSettings:
    """One entry of ``mcp_servers``."""

    Command: str = dataclasses.field(default="", metadata=json_field(name="command"))
    Args: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="args"))
    Env: dict[str, str] = dataclasses.field(default_factory=dict[str, str], metadata=json_field(name="env"))
    CWD: str = dataclasses.field(default="", metadata=json_field(name="cwd"))
    ConnectTimeoutSeconds: int = dataclasses.field(default=0, metadata=json_field(name="connect_timeout_seconds"))
    CallTimeoutSeconds: int = dataclasses.field(default=0, metadata=json_field(name="call_timeout_seconds"))


@dataclasses.dataclass(frozen=True, slots=True)
class SandboxSettings:
    """The ``sandbox`` block of ``settings.json``."""

    Mode: str = dataclasses.field(default="", metadata=json_field(name="mode"))
    CPUSeconds: int = dataclasses.field(default=0, metadata=json_field(name="cpu_seconds"))
    MemoryMB: int = dataclasses.field(default=0, metadata=json_field(name="memory_mb"))
    MaxProcesses: int = dataclasses.field(default=0, metadata=json_field(name="max_processes"))
    MaxOpenFiles: int = dataclasses.field(default=0, metadata=json_field(name="max_open_files"))


@dataclasses.dataclass(frozen=True, slots=True)
class PermissionSettings:
    """The ``permissions`` block of ``settings.json``."""

    Mode: str = dataclasses.field(default="", metadata=json_field(name="mode"))
    AllowTools: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_tools"))
    DenyTools: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_tools"))
    AllowCommandPrefixes: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="allow_command_prefixes")
    )
    DenyCommandPrefixes: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="deny_command_prefixes")
    )
    AllowPaths: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_paths"))
    DenyPaths: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_paths"))
    AllowEnv: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_env"))
    DenyEnv: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_env"))
    Network: str = dataclasses.field(default="", metadata=json_field(name="network"))


@dataclasses.dataclass(frozen=True, slots=True)
class Settings:
    """``~/.superagent/settings.json``, field for field.

    Every field is written, because none of them is ``omitempty``: a template file
    a user opens has to show every knob it can turn.
    """

    Provider: str = dataclasses.field(default="", metadata=json_field(name="provider"))
    Providers: dict[str, ProviderConfig] = dataclasses.field(
        default_factory=dict[str, ProviderConfig], metadata=json_field(name="providers")
    )
    Permissions: PermissionSettings = dataclasses.field(
        default_factory=PermissionSettings, metadata=json_field(name="permissions")
    )
    Sandbox: SandboxSettings = dataclasses.field(default_factory=SandboxSettings, metadata=json_field(name="sandbox"))
    MCPServers: dict[str, MCPServerSettings] = dataclasses.field(
        default_factory=dict[str, MCPServerSettings], metadata=json_field(name="mcp_servers")
    )
    LSPServers: dict[str, LSPServerSettings] = dataclasses.field(
        default_factory=dict[str, LSPServerSettings], metadata=json_field(name="lsp_servers")
    )
    Agent: str = dataclasses.field(default="", metadata=json_field(name="agent"))
    Agents: dict[str, AgentSettings] = dataclasses.field(
        default_factory=dict[str, AgentSettings], metadata=json_field(name="agents")
    )
    Extensions: ExtensionSettings = dataclasses.field(
        default_factory=ExtensionSettings, metadata=json_field(name="extensions")
    )
    Telemetry: TelemetrySettings = dataclasses.field(
        default_factory=TelemetrySettings, metadata=json_field(name="telemetry")
    )


@dataclasses.dataclass(slots=True)
class Config:
    """Everything startup decided, resolved and ready for the session.

    Mutable on purpose: :func:`super_agent.app.session.NewSessionWithExtensions`
    points ``Sandbox.Workspace`` at the workspace's primary root once it knows it.
    """

    Provider: str = ""
    NoTools: bool = False
    PermissionMode: PermissionMode = ZeroPermissionMode
    PermissionRules: PermissionRules = dataclasses.field(default_factory=PermissionRules)
    Sandbox: SandboxConfig = dataclasses.field(default_factory=SandboxConfig)
    MCPServers: list[MCPServerConfig] = dataclasses.field(default_factory=list[MCPServerConfig])
    LSPServers: list[LSPServerConfig] = dataclasses.field(default_factory=list[LSPServerConfig])
    #: The selected provider's configuration, credential included. This is the
    #: value the session builds the model from.
    ModelConfig: ProviderConfig = dataclasses.field(default_factory=ProviderConfig)
    ProviderConfigs: dict[str, ProviderConfig] = dataclasses.field(default_factory=dict[str, ProviderConfig])
    Instructions: Bundle = dataclasses.field(default_factory=Bundle)
    InstructionSources: tuple[str, ...] = ()
    Agents: dict[str, AgentSettings] = dataclasses.field(default_factory=dict[str, AgentSettings])
    Agent: str = ""
    Extensions: Extensions = dataclasses.field(default_factory=Extensions)
    TelemetryPath: str = ""
    Project: project.Project = dataclasses.field(default_factory=project.Project)
    Workspace: Context | None = None
    ConfigRoot: str = ""


def DefaultSettings() -> Settings:
    """The template written on first run, and the source of every default."""
    return Settings(
        Provider="deepseek",
        Providers={
            "deepseek": ProviderConfig(
                BaseURL="https://api.deepseek.com",
                APIKey=DEFAULT_PLACEHOLDER,
                Model="deepseek-reasoner",
            ),
            "openai": ProviderConfig(APIKey=DEFAULT_PLACEHOLDER, Model="gpt-4o"),
            "claude": ProviderConfig(APIKey=CLAUDE_PLACEHOLDER, Model="claude-3-7-sonnet-20250219"),
        },
        Permissions=PermissionSettings(Mode="ask", Network="deny"),
        Sandbox=SandboxSettings(Mode="strict", CPUSeconds=120, MemoryMB=1024, MaxProcesses=128, MaxOpenFiles=256),
        MCPServers={},
        LSPServers={},
        Agent="build",
        Agents={},
        Extensions=ExtensionSettings(Commands={}, Hooks={}),
    )


def LoadConfig(flags: Flags, lookup: Lookup | None = None) -> Config:
    """Combine flags, environment, and settings into one :class:`Config`.

    Every failure here is fatal on purpose: an unknown permission mode, a sandbox
    mode this build cannot honour, or a provider with no usable credential fails
    at startup with a message that names what is missing, rather than surfacing as
    an authentication error on the first turn.
    """
    if lookup is None:
        lookup = os.environ.get
    settings = LoadSettings()
    provider = settings.Provider
    processCWD = os.getcwd()
    selectedProject = project.Resolve(flags.CWD, processCWD)
    workspaceContext = NewDefaultContext(selectedProject.Root)
    cwd = workspaceContext.GetCWD()
    telemetryPath = settings.Telemetry.LogPath
    if telemetryPath == "":
        telemetryPath = os.path.join(os.path.expanduser("~"), USER_CONFIG_DIRECTORY, "telemetry.jsonl")
    elif not os.path.isabs(telemetryPath):
        telemetryPath = os.path.join(cwd, telemetryPath)
    bundle = loadInstructions(cwd)
    extensions = loadExtensions(settings.Extensions, cwd)
    mode = PermissionMode(firstNonEmpty(flags.PermissionMode, settings.Permissions.Mode, "ask"))
    # The YOLO environment variable is a fallback for when no explicit mode was
    # requested; an explicit --approval-mode flag always wins so a checked-in
    # .env cannot silently disable permission prompts.
    if flags.AutoApproveTools and flags.PermissionMode != "":
        raise ValueError(
            "--yolo and --approval-mode are mutually exclusive; use --approval-mode bypass instead of --yolo"
        )
    if flags.AutoApproveTools or (flags.PermissionMode == "" and envTrue(lookup, "YOLO")):
        mode = PermissionModeBypass
    if not ValidPermissionMode(mode):
        raise ValueError("invalid permission mode: " + str(mode))
    sandboxMode = SandboxMode(settings.Sandbox.Mode)
    if not ValidSandboxMode(sandboxMode):
        raise ValueError("invalid sandbox mode: " + settings.Sandbox.Mode)
    rules = PermissionRules(
        AllowTools=settings.Permissions.AllowTools,
        DenyTools=settings.Permissions.DenyTools,
        AllowPrefixes=settings.Permissions.AllowCommandPrefixes,
        DenyPrefixes=settings.Permissions.DenyCommandPrefixes,
        AllowPaths=settings.Permissions.AllowPaths,
        DenyPaths=settings.Permissions.DenyPaths,
        AllowEnv=settings.Permissions.AllowEnv,
        DenyEnv=settings.Permissions.DenyEnv,
        Network=firstNonEmpty(settings.Permissions.Network, "deny"),
    )
    # Settings come from JSON, where object key order is not meaningful, so the
    # server names are sorted to make the resolved order deterministic.
    mcpServers: list[MCPServerConfig] = []
    for name in sorted(settings.MCPServers):
        server = settings.MCPServers[name]
        serverCWD = server.CWD
        if serverCWD == "":
            serverCWD = cwd
        elif not os.path.isabs(serverCWD):
            serverCWD = os.path.join(cwd, serverCWD)
        mcpServers.append(
            MCPServerConfig(
                Name=name,
                Command=server.Command,
                Args=list(server.Args),
                Env=dict(server.Env),
                CWD=serverCWD,
                ConnectTimeout=float(server.ConnectTimeoutSeconds),
                CallTimeout=float(server.CallTimeoutSeconds),
            )
        )
    lspServers: list[LSPServerConfig] = [
        LSPServerConfig(
            Name=name,
            Command=server.Command,
            Args=tuple(server.Args),
            Extensions=tuple(server.Extensions),
            LanguageID=server.LanguageID,
            Root=cwd,
        )
        for name, server in sorted(settings.LSPServers.items())
    ]
    providerConfig = resolveProviderConfig(settings, provider, lookup)
    return Config(
        Provider=provider,
        NoTools=flags.NoTools or envTrue(lookup, "NO_TOOLS"),
        PermissionMode=mode,
        PermissionRules=rules,
        Sandbox=SandboxConfig(
            Mode=sandboxMode,
            Workspace=cwd,
            AllowNetwork=rules.Network == "allow",
            CPUSeconds=settings.Sandbox.CPUSeconds,
            MemoryBytes=settings.Sandbox.MemoryMB << 20,
            MaxProcesses=settings.Sandbox.MaxProcesses,
            MaxOpenFiles=settings.Sandbox.MaxOpenFiles,
        ),
        MCPServers=mcpServers,
        LSPServers=lspServers,
        ModelConfig=providerConfig,
        ProviderConfigs=settings.Providers,
        Instructions=bundle,
        InstructionSources=instructionSourcePaths(bundle),
        Agents=settings.Agents,
        Agent=firstNonEmpty(settings.Agent, "build"),
        Extensions=extensions,
        TelemetryPath=telemetryPath,
        Project=selectedProject,
        Workspace=workspaceContext,
        ConfigRoot=selectedProject.Root,
    )


def apikeyPlaceholder(provider: str) -> str:
    """The value :func:`DefaultSettings` writes for ``provider``.

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
    config = settings.Providers.get(provider)
    if config is None:
        raise ValueError("provider " + provider + " is not configured: add it to the providers map in settings.json")
    if config.APIKey == apikeyPlaceholder(provider):
        config = dataclasses.replace(config, APIKey="")
    if config.APIKey == "":
        envKey = provider.upper() + "_API_KEY"
        value = lookup(envKey)
        if value:
            return dataclasses.replace(config, APIKey=value)
        raise ValueError("provider " + provider + " has no api_key: set it in settings.json or export " + envKey)
    return config


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
    return tuple(source.Path for source in bundle.Sources)


def LoadSettings() -> Settings:
    """Read ``~/.superagent/settings.json``, creating the template if it is absent."""
    return LoadSettingsFile(SettingsPath())


def SettingsPath() -> str:
    """``~/.superagent/settings.json`` — home, and spelled without a hyphen."""
    return os.path.join(os.path.expanduser("~"), USER_CONFIG_DIRECTORY, "settings.json")


def LoadSettingsFile(path: str) -> Settings:
    """Read one settings file, or write the template when there is none.

    An existing file is never overwritten: a parse failure is reported rather than
    repaired, because silently replacing a file the user edited would throw away
    the edit and the error that explains it.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except FileNotFoundError:
        settings = DefaultSettings()
        # The same atomic write a save performs: a half-written settings.json
        # would make every later startup fail to parse it.
        SaveSettingsFile(path, settings)
        return settings
    settings = jsonutil.loads(content, Settings)
    return normalizeSettings(dataclasses.replace(settings, Extensions=decodeExtensions(content)))


def SaveSettingsFile(path: str, settings: Settings) -> None:
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
    defaults = DefaultSettings().Sandbox
    permissions = settings.Permissions or PermissionSettings()
    sandbox = settings.Sandbox or SandboxSettings()
    return dataclasses.replace(
        settings,
        Providers=settings.Providers or {},
        Agents=settings.Agents or {},
        LSPServers=settings.LSPServers or {},
        MCPServers=settings.MCPServers or {},
        Extensions=settings.Extensions or ExtensionSettings(),
        Telemetry=settings.Telemetry or TelemetrySettings(),
        Agent=settings.Agent or "build",
        Permissions=dataclasses.replace(
            permissions,
            Mode=permissions.Mode or "ask",
            Network=permissions.Network or "deny",
        ),
        Sandbox=dataclasses.replace(
            sandbox,
            Mode=sandbox.Mode or defaults.Mode,
            CPUSeconds=sandbox.CPUSeconds if sandbox.CPUSeconds > 0 else defaults.CPUSeconds,
            MemoryMB=sandbox.MemoryMB if sandbox.MemoryMB > 0 else defaults.MemoryMB,
            MaxProcesses=sandbox.MaxProcesses if sandbox.MaxProcesses > 0 else defaults.MaxProcesses,
            MaxOpenFiles=sandbox.MaxOpenFiles if sandbox.MaxOpenFiles > 0 else defaults.MaxOpenFiles,
        ),
    )


def settingsMap(configs: list[MCPServerConfig]) -> dict[str, MCPServerSettings]:
    """The settings-shaped view of the configured MCP servers."""
    result: dict[str, MCPServerSettings] = {}
    for config in configs:
        result[config.Name] = MCPServerSettings(
            Command=config.Command,
            Args=tuple(config.Args),
            Env=dict(config.Env),
            CWD=config.CWD,
            ConnectTimeoutSeconds=int(config.ConnectTimeout),
            CallTimeoutSeconds=int(config.CallTimeout),
        )
    return result
