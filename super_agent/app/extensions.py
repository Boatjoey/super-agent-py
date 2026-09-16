"""Commands, hooks, skills, and plugins discovered from the two extension roots.

The roots are ``~/.superagent`` and ``<cwd>/.superagent``, both spelled **without**
a hyphen. The hyphenated ``.super-agent`` spelling belongs to worktrees and
exports and must never be used here.

Discovery order decides precedence: an explicitly configured command wins over a
discovered one, and the user root wins over the project root. A plugin that
redeclares an existing command is an error rather than a silent override, because
two files claiming the same command name is a mistake worth surfacing.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Final

from super_agent import jsonutil
from super_agent.jsonutil import json_field

#: The largest extension file that is read, in bytes.
MAX_EXTENSION_FILE_SIZE: Final[int] = 128 << 10

#: The user-level configuration directory spelling: no hyphen, under home.
USER_CONFIG_DIRECTORY: Final[str] = ".superagent"

#: The project-level extensions directory spelling: no hyphen, under the cwd.
PROJECT_EXTENSIONS_DIRECTORY: Final[str] = ".superagent"

#: The hook events an extension may subscribe to. Anything else is a typo, not a
#: hook, so it fails the load rather than being ignored.
HOOK_EVENTS: Final[tuple[str, ...]] = (
    "startup",
    "session_start",
    "before_turn",
    "pre_tool",
    "post_tool",
    "approval_requested",
    "turn_complete",
    "after_turn",
    "error",
)

#: Command names the built-in catalogue owns. A custom command may not take one.
RESERVED_COMMAND_NAMES: Final[frozenset[str]] = frozenset(
    {
        "agent",
        "attach",
        "attachments",
        "branch",
        "build",
        "clear",
        "commands",
        "commit-message",
        "compact",
        "delete-session",
        "diagnostics",
        "diff",
        "export",
        "fix-ci",
        "forget",
        "fork",
        "help",
        "instructions",
        "mcp",
        "memory",
        "mode",
        "permissions",
        "plan",
        "plugins",
        "quit",
        "remember",
        "rename",
        "reset",
        "resume",
        "review",
        "sessions",
        "share",
        "skills",
        "undo",
    }
)


@dataclasses.dataclass(frozen=True, slots=True)
class ExtensionSettings:
    """The ``extensions`` block of ``settings.json``."""

    commands: dict[str, str] = dataclasses.field(default_factory=dict[str, str], metadata=json_field(name="commands"))
    hooks: dict[str, tuple[str, ...]] = dataclasses.field(
        default_factory=dict[str, tuple[str, ...]], metadata=json_field(name="hooks")
    )
    skills: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="skills"))
    plugins: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="plugins"))


@dataclasses.dataclass(frozen=True, slots=True)
class PluginManifest:
    """``plugin.json``, which contributes commands, hooks, and skills."""

    commands: dict[str, str] = dataclasses.field(default_factory=dict[str, str], metadata=json_field(name="commands"))
    hooks: dict[str, tuple[str, ...]] = dataclasses.field(
        default_factory=dict[str, tuple[str, ...]], metadata=json_field(name="hooks")
    )
    skills: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="skills"))


@dataclasses.dataclass(frozen=True, slots=True)
class Extensions:
    """Everything the extension roots and settings together provide."""

    commands: dict[str, str] = dataclasses.field(default_factory=dict[str, str])
    hooks: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict[str, tuple[str, ...]])
    skill_prompt: str = ""
    skills: tuple[str, ...] = ()
    plugins: tuple[str, ...] = ()


def loadExtensions(settings: ExtensionSettings, cwd: str) -> Extensions:
    """Resolve the effective extensions for ``cwd``."""
    commands = cloneStringMap(settings.commands)
    hooks = cloneHooks(settings.hooks)
    skill_paths = list(settings.skills)

    home = os.path.expanduser("~")
    roots = (
        os.path.join(home, USER_CONFIG_DIRECTORY),
        os.path.join(cwd, PROJECT_EXTENSIONS_DIRECTORY),
    )
    for root in roots:
        for name, prompt in discoverCommands(os.path.join(root, "commands")).items():
            commands.setdefault(name, prompt)
        skill_paths.extend(discoverSkills(os.path.join(root, "skills")))

    plugin_paths = list(settings.plugins)
    for root in (
        os.path.join(home, USER_CONFIG_DIRECTORY, "plugins"),
        os.path.join(cwd, PROJECT_EXTENSIONS_DIRECTORY, "plugins"),
    ):
        for entry in _listdir(root):
            if os.path.isdir(os.path.join(root, entry)):
                plugin_paths.append(os.path.join(root, entry))

    seen_plugins: dict[str, bool] = {}
    for plugin_path in plugin_paths:
        plugin_root = resolveConfigPath(cwd, plugin_path)
        if seen_plugins.get(plugin_root):
            continue
        seen_plugins[plugin_root] = True
        content = readExtensionFile(os.path.join(plugin_root, "plugin.json"))
        try:
            parsed = jsonutil.loads(content, PluginManifest)
        except (ValueError, TypeError) as error:
            raise ValueError(f"decode plugin {plugin_path}: {error}") from error
        for name, prompt in parsed.commands.items():
            name = name.strip().removeprefix("/")
            if name in commands:
                raise ValueError("duplicate custom command: " + name)
            commands[name] = prompt
        for event, event_commands in parsed.hooks.items():
            hooks[event] = (*hooks.get(event, ()), *event_commands)
        for path in parsed.skills:
            skill_paths.append(os.path.join(plugin_root, path))

    _validate(commands, hooks)

    skills: list[str] = []
    skill_names: list[str] = []
    for skill_path in skill_paths:
        path = resolveConfigPath(cwd, skill_path)
        if os.path.basename(path) != "SKILL.md":
            path = os.path.join(path, "SKILL.md")
        content = readExtensionFile(path)
        skills.append("Skill: " + os.path.basename(os.path.dirname(path)) + "\n" + content.strip())
        skill_names.append(os.path.basename(os.path.dirname(path)))

    plugin_names = sorted({os.path.basename(path) for path in seen_plugins})
    return Extensions(
        commands=commands,
        hooks=hooks,
        skill_prompt="\n\n".join(skills),
        skills=tuple(sorted(skill_names)),
        plugins=tuple(plugin_names),
    )


def _validate(commands: dict[str, str], hooks: dict[str, tuple[str, ...]]) -> None:
    for name, prompt in commands.items():
        if name == "" or any(character in name for character in " \t\n"):
            raise ValueError("invalid custom command name: " + name)
        if name in RESERVED_COMMAND_NAMES:
            raise ValueError("custom command is reserved: " + name)
        if prompt.strip() == "":
            raise ValueError("custom command prompt is empty: " + name)
    for event in hooks:
        if event not in HOOK_EVENTS:
            raise ValueError("unknown hook event: " + event)


def discoverCommands(directory: str) -> dict[str, str]:
    """Every ``*.md`` file in ``directory``, keyed by its basename."""
    result: dict[str, str] = {}
    for entry in _listdir(directory):
        if os.path.splitext(entry)[1] != ".md":
            continue
        content = readExtensionFile(os.path.join(directory, entry))
        result[os.path.splitext(entry)[0]] = content.strip()
    return result


def discoverSkills(directory: str) -> list[str]:
    """Every subdirectory of ``directory`` that holds a ``SKILL.md``."""
    result: list[str] = []
    for entry in _listdir(directory):
        path = os.path.join(directory, entry, "SKILL.md")
        if os.path.isfile(path):
            result.append(path)
    return result


def readExtensionFile(path: str) -> str:
    """Read an extension file, refusing anything over the size cap."""
    size = os.path.getsize(path)
    if size > MAX_EXTENSION_FILE_SIZE:
        raise ValueError("extension file exceeds 128 KiB")
    with open(path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def resolveConfigPath(cwd: str, path: str) -> str:
    """Resolve an extension-configured path: absolute, or relative to ``cwd``."""
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(cwd, path))


def cloneStringMap(input_map: dict[str, str]) -> dict[str, str]:
    """Copy a command map, normalising each key the way a user would write it."""
    return {name.strip().removeprefix("/"): value for name, value in input_map.items()}


def cloneHooks(input_hooks: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    return {event: tuple(commands) for event, commands in input_hooks.items()}


def _listdir(directory: str) -> list[str]:
    """Sorted entries, or nothing when the directory does not exist."""
    try:
        return sorted(os.listdir(directory))
    except FileNotFoundError:
        return []


__all__ = [
    "HOOK_EVENTS",
    "MAX_EXTENSION_FILE_SIZE",
    "PROJECT_EXTENSIONS_DIRECTORY",
    "RESERVED_COMMAND_NAMES",
    "USER_CONFIG_DIRECTORY",
    "ExtensionSettings",
    "Extensions",
    "PluginManifest",
    "cloneHooks",
    "cloneStringMap",
    "discoverCommands",
    "discoverSkills",
    "loadExtensions",
    "readExtensionFile",
    "resolveConfigPath",
]
