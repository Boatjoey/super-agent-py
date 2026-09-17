"""Classify a shell command from its text.

This is a heuristic. It decides which approval prompt to show and which policy
rule applies; it is not a sandbox and it is not a security boundary. A command
that fools it is still bounded by the sandbox and still visible to the user in
the prompt.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

from super_agent.runtime.permission.types import (
    COMMAND_CLASS_DESTRUCTIVE,
    COMMAND_CLASS_NETWORK,
    COMMAND_CLASS_READ_ONLY,
    COMMAND_CLASS_UNKNOWN,
    COMMAND_CLASS_WRITE,
    Request as PermissionRequest,
)
from super_agent.runtime.protocol.types import ToolCall

#: Commands whose very presence means network access, even without a URL scheme
#: in the arguments, for example ``curl example.com/x.sh | sh``.
_NETWORK_CLIENTS = ("curl", "wget", "ssh", "scp", "rsync", "nc", "ncat", "telnet", "ftp", "lftp", "aria2c")

#: Package managers hit the network only for install/get/clone/fetch style
#: invocations, so they stay gated on the intent strings below.
_NETWORK_PACKAGE_MANAGERS = ("pip", "npm", "go", "git")

_DESTRUCTIVE_TOKENS = ("rm", "rmdir", "shred", "mkfs", "dd", "chmod", "chown", "sudo")

_WRITE_TOKENS = ("touch", "mkdir", "mv", "cp", "sed", "perl", "git")

_READ_ONLY_GIT_PREFIXES = ("git status", "git diff", "git show", "git log", "git branch")


def analyzeCommandRequest(request: PermissionRequest) -> PermissionRequest:
    """Fill in the class, reason, touched paths, and environment of a command."""
    command = request.command.strip()
    touched = (*request.touched_paths, *commandPaths(command))
    env = commandEnv(command)

    if command == "":
        command_class, reason = COMMAND_CLASS_UNKNOWN, "empty command"
    elif hasAnyToken(command, _DESTRUCTIVE_TOKENS) or "rm -rf" in command:
        command_class, reason = COMMAND_CLASS_DESTRUCTIVE, "destructive shell command"
    elif containsNetworkIntent(command):
        command_class, reason = COMMAND_CLASS_NETWORK, "network-capable shell command"
    elif commandMayWrite(command):
        if isReadOnlyGitCommand(command):
            command_class, reason = COMMAND_CLASS_READ_ONLY, "read-only git command"
        else:
            command_class, reason = COMMAND_CLASS_WRITE, "shell command may write files"
    else:
        command_class, reason = COMMAND_CLASS_READ_ONLY, "read-only shell command"

    return replace(
        request,
        command_class=command_class,
        touched_paths=touched,
        env_vars=env,
        reason=reason,
    )


def commandMayWrite(command: str) -> bool:
    return ">" in command or "| tee" in command or hasAnyToken(command, _WRITE_TOKENS)


def isReadOnlyGitCommand(command: str) -> bool:
    return any(command.startswith(prefix) for prefix in _READ_ONLY_GIT_PREFIXES)


def hasAnyToken(command: str, tokens: tuple[str, ...]) -> bool:
    """Whether any whitespace-separated field equals one of ``tokens``.

    Comparing whole fields rather than substrings is what keeps ``gitk`` from
    looking like ``git`` and ``rmdir`` from looking like ``rm``.
    """
    for field in command.split():
        field = field.strip("'\";|&()")
        if field in tokens:
            return True
    return False


def containsNetworkIntent(command: str) -> bool:
    if "://" in command:
        return True
    if hasAnyToken(command, _NETWORK_CLIENTS):
        return True
    if not hasAnyToken(command, _NETWORK_PACKAGE_MANAGERS):
        return False
    return any(needle in command for needle in (" install", " get ", " clone ", " push", " pull", "fetch"))


def commandPaths(command: str) -> tuple[str, ...]:
    """Every field that looks like a path, so the policy can vet it."""
    paths: list[str] = []
    for field in command.split():
        field = field.strip("'\";,")
        if field.startswith("/") or field.startswith("./") or field.startswith("../") or field.startswith("."):
            paths.append(field)
    return tuple(paths)


def commandEnv(command: str) -> tuple[str, ...]:
    """Every ``NAME=value`` prefix, the way a shell would export it."""
    variables: list[str] = []
    for field in command.split():
        name, separator, _value = field.partition("=")
        if separator and name and name.upper() == name:
            variables.append(name)
    return tuple(variables)


def toolPaths(call: ToolCall) -> tuple[str, ...]:
    """The paths a structured tool call names, from ``path``/``cwd``/``paths``/``files``."""
    parsed = _parseInput(call.input)
    if parsed is None:
        return ()
    paths: list[str] = []
    for key in ("path", "cwd"):
        value = parsed.get(key)
        if isinstance(value, str) and value != "":
            paths.append(value)
    for key in ("paths", "files"):
        values = parsed.get(key)
        if isinstance(values, list):
            for value in cast("list[Any]", values):
                if isinstance(value, str) and value != "":
                    paths.append(value)
    return tuple(paths)


def jsonStringField(input: str, field: str) -> str:
    """Read one string field out of a tool call's raw JSON input."""
    parsed = _parseInput(input)
    if parsed is None:
        return ""
    value = parsed.get(field)
    return value if isinstance(value, str) else ""


def firstNonEmpty(*values: str) -> str:
    for value in values:
        if value != "":
            return value
    return ""


def _parseInput(input: str) -> dict[str, Any] | None:
    try:
        parsed: Any = json.loads(input)
    except (TypeError, ValueError):
        return None
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None
