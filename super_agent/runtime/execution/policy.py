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
    CommandClassDestructive,
    CommandClassNetwork,
    CommandClassReadOnly,
    CommandClassUnknown,
    CommandClassWrite,
    Request as PermissionRequest,
)
from super_agent.runtime.protocol.types import ToolCall, ToolSpec


class ToolDecision(IntEnum):
    """What to do with one tool call."""

    DecisionNeedsApproval = 0
    DecisionRunDirectly = 1
    DecisionDenied = 2


class PermissionMode(str):
    """How much the policy trusts tool calls. Mirrors Go's string-backed type."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"PermissionMode({str.__repr__(self)})"


PermissionModeAsk: Final[PermissionMode] = PermissionMode("ask")
PermissionModeAcceptEdits: Final[PermissionMode] = PermissionMode("accept-edits")
PermissionModePlan: Final[PermissionMode] = PermissionMode("plan")
PermissionModeBypass: Final[PermissionMode] = PermissionMode("bypass")

#: Go's zero value for the type. :func:`NewPolicy` reads it as "use the default".
ZeroPermissionMode: Final[PermissionMode] = PermissionMode("")


@dataclasses.dataclass(frozen=True, slots=True)
class PermissionRules:
    """The operator-configured allow and deny lists."""

    AllowTools: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_tools", omitempty=True))
    DenyTools: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_tools", omitempty=True))
    AllowPrefixes: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="allow_command_prefixes", omitempty=True)
    )
    DenyPrefixes: tuple[str, ...] = dataclasses.field(
        default=(), metadata=json_field(name="deny_command_prefixes", omitempty=True)
    )
    AllowPaths: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_paths", omitempty=True))
    DenyPaths: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_paths", omitempty=True))
    AllowEnv: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="allow_env", omitempty=True))
    DenyEnv: tuple[str, ...] = dataclasses.field(default=(), metadata=json_field(name="deny_env", omitempty=True))
    Network: str = dataclasses.field(default="", metadata=json_field(name="network", omitempty=True))


@dataclasses.dataclass(frozen=True, slots=True)
class ToolPolicyInput:
    """What the policy needs to classify a call beyond the call itself."""

    ToolSpecs: tuple[ToolSpec, ...] = ()
    CWD: str = ""


class Policy(Protocol):
    """Decides what happens to a tool call."""

    def ClassifyToolCall(self, call: ToolCall, input: ToolPolicyInput) -> ToolDecision: ...

    def PermissionRequest(self, call: ToolCall, input: ToolPolicyInput) -> PermissionRequest: ...


def ValidPermissionMode(mode: PermissionMode) -> bool:
    """Whether ``mode`` is one of the four accepted modes."""
    return mode in (
        PermissionModeAsk,
        PermissionModeAcceptEdits,
        PermissionModePlan,
        PermissionModeBypass,
    )


def NewDefaultPolicy() -> DefaultPolicy:
    return NewPolicy(PermissionModeAsk, PermissionRules())


def NewPolicy(mode: PermissionMode, rules: PermissionRules) -> DefaultPolicy:
    """Build a policy, normalising the two fields that have a canonical form."""
    if mode == "":
        mode = PermissionModeAsk
    normalised = dataclasses.replace(rules, Network=rules.Network.strip().lower())
    return DefaultPolicy(_mode=mode, _rules=normalised)


@dataclasses.dataclass(slots=True)
class DefaultPolicy:
    """The only policy; the port exists so tests can substitute one."""

    _mode: PermissionMode = PermissionModeAsk
    _rules: PermissionRules = dataclasses.field(default_factory=PermissionRules)

    def Mode(self) -> PermissionMode:
        return self._mode

    def Rules(self) -> PermissionRules:
        return self._rules

    def ClassifyToolCall(self, call: ToolCall, input: ToolPolicyInput) -> ToolDecision:
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
        request = self.PermissionRequest(call, input)
        if (
            self.matches(call.Name, self._rules.DenyTools)
            or self.matchesPrefix(request.Command, self._rules.DenyPrefixes)
            or self.touchesProtectedPath(request)
            or self.touchesDeniedPath(request)
            or self.usesDeniedEnv(request)
        ):
            return ToolDecision.DecisionDenied
        if self._mode == PermissionModeBypass:
            return ToolDecision.DecisionRunDirectly
        if self._mode == PermissionModePlan:
            if request.CommandClass == CommandClassReadOnly and not self.needsApproval(call, input.ToolSpecs):
                return ToolDecision.DecisionRunDirectly
            return ToolDecision.DecisionDenied
        if request.CommandClass == CommandClassDestructive or self.networkDenied(request):
            return ToolDecision.DecisionNeedsApproval
        if (
            self.matches(call.Name, self._rules.AllowTools)
            or self.matchesPrefix(request.Command, self._rules.AllowPrefixes)
            or self.pathsAllowed(request)
            or self.envAllowed(request)
        ):
            return ToolDecision.DecisionRunDirectly
        if self._mode == PermissionModeAcceptEdits:
            if request.CommandClass == CommandClassReadOnly:
                return ToolDecision.DecisionRunDirectly
            if self.isWriteTool(call.Name):
                return ToolDecision.DecisionRunDirectly
        if self.needsApproval(call, input.ToolSpecs):
            return ToolDecision.DecisionNeedsApproval
        return ToolDecision.DecisionRunDirectly

    def PermissionRequest(self, call: ToolCall, input: ToolPolicyInput) -> PermissionRequest:
        """Describe the call the way an approval prompt should show it."""
        request = PermissionRequest(
            ToolName=call.Name,
            CommandClass=CommandClassUnknown,
            CWD=input.CWD or ".",
            Reason="unknown tool calls require approval",
        )

        if call.Name in ("web_search", "browser_fetch"):
            return replace(request, CommandClass=CommandClassNetwork, Reason="network access requires approval")
        if call.Name in ("bash", "run_command"):
            command = jsonStringField(call.Input, "command")
            if call.Name == "run_command":
                cwd = firstNonEmpty(jsonStringField(call.Input, "cwd"), request.CWD)
                return analyzeCommandRequest(replace(request, Command=command, CWD=cwd))
            return analyzeCommandRequest(replace(request, Command=command))
        if call.Name == "go_test":
            return replace(
                request,
                Command="go test",
                CWD=firstNonEmpty(jsonStringField(call.Input, "cwd"), request.CWD),
                CommandClass=CommandClassReadOnly,
                Reason="go test is read-only",
            )
        if call.Name in ("git_status", "git_diff", "read_file", "list_files", "search"):
            return replace(
                request,
                CommandClass=CommandClassReadOnly,
                TouchedPaths=toolPaths(call),
                Reason="read-only tool",
            )
        if call.Name in ("write_file", "apply_patch", "format"):
            return replace(
                request,
                CommandClass=CommandClassWrite,
                TouchedPaths=toolPaths(call),
                Reason="tool writes workspace files",
            )
        if not self.needsApproval(call, input.ToolSpecs):
            return replace(request, CommandClass=CommandClassReadOnly, Reason="tool spec is marked safe")
        return request

    def needsApproval(self, call: ToolCall, specs: tuple[ToolSpec, ...]) -> bool:
        return isRiskyTool(call.Name, specs)

    def isWriteTool(self, name: str) -> bool:
        return name in ("write_file", "apply_patch", "format")

    def networkDenied(self, request: PermissionRequest) -> bool:
        return self._rules.Network != "allow" and request.CommandClass == CommandClassNetwork

    def touchesProtectedPath(self, request: PermissionRequest) -> bool:
        """Whether the call reaches outside the sandbox's ordinary reach.

        ``.git``, ``.env``, SSH keys, AWS credentials, and gcloud configuration
        are refused outright, and so is any absolute path: the workspace is the
        only place a tool may write.
        """
        for path in request.TouchedPaths:
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
            if os.path.isabs(path) and request.CWD != "":
                rel = _rel(request.CWD, path)
                if rel is None or rel == ".." or rel.startswith(".." + os.sep):
                    return True
        return False

    def touchesDeniedPath(self, request: PermissionRequest) -> bool:
        return any(self.matchesPath(path, self._rules.DenyPaths) for path in request.TouchedPaths)

    def usesDeniedEnv(self, request: PermissionRequest) -> bool:
        return any(self.matches(key, self._rules.DenyEnv) for key in request.EnvVars)

    def pathsAllowed(self, request: PermissionRequest) -> bool:
        if not request.TouchedPaths or not self._rules.AllowPaths:
            return False
        return all(self.matchesPath(path, self._rules.AllowPaths) for path in request.TouchedPaths)

    def envAllowed(self, request: PermissionRequest) -> bool:
        if not request.EnvVars or not self._rules.AllowEnv:
            return False
        return all(self.matches(key, self._rules.AllowEnv) for key in request.EnvVars)

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
        if spec.Name == name:
            return spec.Risky
    return True


def _cleanSlash(path: str) -> str:
    """``filepath.ToSlash(filepath.Clean(path))``."""
    return os.path.normpath(path).replace(os.sep, "/")


def _rel(basepath: str, targpath: str) -> str | None:
    """``filepath.Rel``, including its refusal to mix absolute and relative paths.

    ``os.path.relpath`` would silently consult the process working directory,
    which is exactly what Go's ``Rel`` refuses to do.
    """
    if os.path.isabs(basepath) != os.path.isabs(targpath):
        return None
    try:
        return os.path.relpath(targpath, basepath)
    except ValueError:
        return None
