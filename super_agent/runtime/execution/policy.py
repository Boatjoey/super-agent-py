"""The permission policy.

Command classification routes approvals; it is not a security boundary. The rules
here decide which tool calls run directly, which need approval, and which are
refused outright — and every refusal is fed back to the model as a tool result so
it can choose another action.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import replace
from enum import IntEnum
from typing import Final, Protocol

from super_agent.jsonutil import json_field
from super_agent.runtime.execution.command_analyzer import (
    analyzeCommandRequest,
    firstNonEmpty,
    jsonStringField,
    toolPaths,
)
from super_agent.runtime.permission.types import (
    COMMAND_CLASS_DESTRUCTIVE,
    COMMAND_CLASS_NETWORK,
    COMMAND_CLASS_READ_ONLY,
    COMMAND_CLASS_UNKNOWN,
    COMMAND_CLASS_WRITE,
    Request as PermissionRequest,
)
from super_agent.runtime.protocol.types import ToolCall, ToolSpec


class ToolDecision(IntEnum):
    """What to do with one tool call."""

    DECISION_NEEDS_APPROVAL = 0
    DECISION_RUN_DIRECTLY = 1
    DECISION_DENIED = 2


class PermissionMode(str):
    """How much the policy trusts tool calls, as a string-backed type."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"PermissionMode({str.__repr__(self)})"


PERMISSION_MODE_ASK: Final[PermissionMode] = PermissionMode("ask")
PERMISSION_MODE_ACCEPT_EDITS: Final[PermissionMode] = PermissionMode("accept-edits")
PERMISSION_MODE_PLAN: Final[PermissionMode] = PermissionMode("plan")
PERMISSION_MODE_BYPASS: Final[PermissionMode] = PermissionMode("bypass")

#: The zero value for the type. :func:`new_policy` reads it as "use the default".
ZERO_PERMISSION_MODE: Final[PermissionMode] = PermissionMode("")


@dataclasses.dataclass(frozen=True, slots=True)
class PermissionRules:
    """The operator-configured allow and deny lists."""

    allow_tools: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="allow_tools", omitempty=True)
    )
    deny_tools: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_tools", omitempty=True))
    allow_prefixes: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="allow_command_prefixes", omitempty=True)
    )
    deny_prefixes: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="deny_command_prefixes", omitempty=True)
    )
    allow_paths: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="allow_paths", omitempty=True)
    )
    deny_paths: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_paths", omitempty=True))
    allow_env: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_env", omitempty=True))
    deny_env: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_env", omitempty=True))
    network: str = dataclasses.field(default="", metadata=json_field(name="network", omitempty=True))


@dataclasses.dataclass(frozen=True, slots=True)
class ToolPolicyInput:
    """What the policy needs to classify a call beyond the call itself."""

    tool_specs: tuple[ToolSpec, ...] = ()
    cwd: str = ""


class Policy(Protocol):
    """Decides what happens to a tool call."""

    def classify_tool_call(self, call: ToolCall, input: ToolPolicyInput) -> ToolDecision: ...

    def permission_request(self, call: ToolCall, input: ToolPolicyInput) -> PermissionRequest: ...


def valid_permission_mode(mode: PermissionMode) -> bool:
    """Whether ``mode`` is one of the four accepted modes."""
    return mode in (
        PERMISSION_MODE_ASK,
        PERMISSION_MODE_ACCEPT_EDITS,
        PERMISSION_MODE_PLAN,
        PERMISSION_MODE_BYPASS,
    )


def new_default_policy() -> DefaultPolicy:
    return new_policy(PERMISSION_MODE_ASK, PermissionRules())


def new_policy(mode: PermissionMode, rules: PermissionRules) -> DefaultPolicy:
    """Build a policy, normalising the two fields that have a canonical form."""
    if mode == "":
        mode = PERMISSION_MODE_ASK
    normalised = dataclasses.replace(rules, network=rules.network.strip().lower())
    return DefaultPolicy(_mode=mode, _rules=normalised)


@dataclasses.dataclass(slots=True)
class DefaultPolicy:
    """The only policy; the port exists so tests can substitute one."""

    _mode: PermissionMode = PERMISSION_MODE_ASK
    _rules: PermissionRules = dataclasses.field(default_factory=PermissionRules)

    def mode(self) -> PermissionMode:
        return self._mode

    def rules(self) -> PermissionRules:
        return self._rules

    def classify_tool_call(self, call: ToolCall, input: ToolPolicyInput) -> ToolDecision:
        """Resolve a tool call in the documented precedence order.

        1. deny rules (absolute — no mode or allow rule overrides them)
        2. bypass mode (runs everything not denied)
        3. plan mode (read-only safe tools only, everything else denied)
        4. destructive commands and network access (approval required, even when
           an allow rule matches: an explicit allow list approves ordinary risky
           work but never silently promotes destructive or network commands)
        5. allow rules (skip the ordinary risky-tool approval)
        6. mode defaults and risky-tool approval
        """
        request = self.permission_request(call, input)
        if (
            self.matches(call.name, self._rules.deny_tools)
            or self.matchesPrefix(request.command, self._rules.deny_prefixes)
            or self.touchesProtectedPath(request)
            or self.touchesDeniedPath(request)
            or self.usesDeniedEnv(request)
        ):
            return ToolDecision.DECISION_DENIED
        if self._mode == PERMISSION_MODE_BYPASS:
            return ToolDecision.DECISION_RUN_DIRECTLY
        if self._mode == PERMISSION_MODE_PLAN:
            if request.command_class == COMMAND_CLASS_READ_ONLY and not self.needsApproval(call, input.tool_specs):
                return ToolDecision.DECISION_RUN_DIRECTLY
            return ToolDecision.DECISION_DENIED
        if request.command_class == COMMAND_CLASS_DESTRUCTIVE or self.networkDenied(request):
            return ToolDecision.DECISION_NEEDS_APPROVAL
        if (
            self.matches(call.name, self._rules.allow_tools)
            or self.matchesPrefix(request.command, self._rules.allow_prefixes)
            or self.pathsAllowed(request)
            or self.envAllowed(request)
        ):
            return ToolDecision.DECISION_RUN_DIRECTLY
        if self._mode == PERMISSION_MODE_ACCEPT_EDITS:
            if request.command_class == COMMAND_CLASS_READ_ONLY:
                return ToolDecision.DECISION_RUN_DIRECTLY
            if self.isWriteTool(call.name):
                return ToolDecision.DECISION_RUN_DIRECTLY
        if self.needsApproval(call, input.tool_specs):
            return ToolDecision.DECISION_NEEDS_APPROVAL
        return ToolDecision.DECISION_RUN_DIRECTLY

    def permission_request(self, call: ToolCall, input: ToolPolicyInput) -> PermissionRequest:
        """Describe the call the way an approval prompt should show it."""
        request = PermissionRequest(
            tool_name=call.name,
            command_class=COMMAND_CLASS_UNKNOWN,
            cwd=input.cwd or ".",
            reason="unknown tool calls require approval",
        )

        if call.name in ("web_search", "browser_fetch"):
            return replace(request, command_class=COMMAND_CLASS_NETWORK, reason="network access requires approval")
        if call.name in ("bash", "run_command"):
            command = jsonStringField(call.input, "command")
            if call.name == "run_command":
                cwd = firstNonEmpty(jsonStringField(call.input, "cwd"), request.cwd)
                return analyzeCommandRequest(replace(request, command=command, cwd=cwd))
            return analyzeCommandRequest(replace(request, command=command))
        if call.name == "go_test":
            return replace(
                request,
                command="go test",
                cwd=firstNonEmpty(jsonStringField(call.input, "cwd"), request.cwd),
                command_class=COMMAND_CLASS_READ_ONLY,
                reason="go test is read-only",
            )
        if call.name in ("git_status", "git_diff", "read_file", "list_files", "search"):
            return replace(
                request,
                command_class=COMMAND_CLASS_READ_ONLY,
                touched_paths=toolPaths(call),
                reason="read-only tool",
            )
        if call.name in ("write_file", "apply_patch", "format"):
            return replace(
                request,
                command_class=COMMAND_CLASS_WRITE,
                touched_paths=toolPaths(call),
                reason="tool writes workspace files",
            )
        if not self.needsApproval(call, input.tool_specs):
            return replace(request, command_class=COMMAND_CLASS_READ_ONLY, reason="tool spec is marked safe")
        return request

    def needsApproval(self, call: ToolCall, specs: tuple[ToolSpec, ...]) -> bool:
        return isRiskyTool(call.name, specs)

    def isWriteTool(self, name: str) -> bool:
        return name in ("write_file", "apply_patch", "format")

    def networkDenied(self, request: PermissionRequest) -> bool:
        return self._rules.network != "allow" and request.command_class == COMMAND_CLASS_NETWORK

    def touchesProtectedPath(self, request: PermissionRequest) -> bool:
        """Whether the call reaches outside the sandbox's ordinary reach.

        ``.git``, ``.env``, SSH keys, AWS credentials, and gcloud configuration
        are refused outright, and so is any absolute path: the workspace is the
        only place a tool may write.
        """
        for path in request.touched_paths:
            clean = _cleanSlash(path)
            if (
                clean == ".git"
                or clean.startswith(".git/")
                or clean == ".env"
                or "/.env" in clean
                or ".ssh/" in clean
                or ".aws/" in clean
                or ".config/gcloud/" in clean
            ):
                return True
            if os.path.isabs(path) and request.cwd != "":
                rel = _rel(request.cwd, path)
                if rel is None or rel == ".." or rel.startswith(".." + os.sep):
                    return True
        return False

    def touchesDeniedPath(self, request: PermissionRequest) -> bool:
        return any(self.matchesPath(path, self._rules.deny_paths) for path in request.touched_paths)

    def usesDeniedEnv(self, request: PermissionRequest) -> bool:
        return any(self.matches(key, self._rules.deny_env) for key in request.env_vars)

    def pathsAllowed(self, request: PermissionRequest) -> bool:
        if not request.touched_paths or not self._rules.allow_paths:
            return False
        return all(self.matchesPath(path, self._rules.allow_paths) for path in request.touched_paths)

    def envAllowed(self, request: PermissionRequest) -> bool:
        if not request.env_vars or not self._rules.allow_env:
            return False
        return all(self.matches(key, self._rules.allow_env) for key in request.env_vars)

    def matches(self, value: str, patterns: tuple[str, ...]) -> bool:
        return any(pattern == value for pattern in patterns)

    def matchesPrefix(self, command: str, prefixes: tuple[str, ...]) -> bool:
        """Whether ``command`` starts with ``prefix`` at a token boundary.

        ``"git"`` matches ``"git status"`` and ``"git"`` but not ``"gitk"``.
        """
        command = command.strip()
        for prefix in prefixes:
            prefix = prefix.strip()
            if prefix == "" or not command.startswith(prefix):
                continue
            rest = command[len(prefix) :]
            if rest == "" or rest[0] in (" ", "\t"):
                return True
        return False

    def matchesPath(self, path: str, patterns: tuple[str, ...]) -> bool:
        path = _cleanSlash(path)
        for pattern in patterns:
            pattern = _cleanSlash(pattern)
            if path == pattern or path.startswith(pattern.rstrip("/") + "/"):
                return True
        return False


def isRiskyTool(name: str, specs: tuple[ToolSpec, ...]) -> bool:
    """Whether a tool needs approval, defaulting to yes for an unknown tool."""
    for spec in specs:
        if spec.name == name:
            return spec.risky
    return True


def _cleanSlash(path: str) -> str:
    """``filepath.ToSlash(filepath.Clean(path))``."""
    return os.path.normpath(path).replace(os.sep, "/")


def _rel(basepath: str, targpath: str) -> str | None:
    """A relative path, refusing to mix absolute and relative inputs.

    ``os.path.relpath`` would silently consult the process working directory,
    which this refuses to do.
    """
    if os.path.isabs(basepath) != os.path.isabs(targpath):
        return None
    try:
        return os.path.relpath(targpath, basepath)
    except ValueError:
        return None
