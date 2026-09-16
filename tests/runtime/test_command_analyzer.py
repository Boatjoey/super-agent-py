"""The shell-command classification table.

The analyzer is otherwise exercised only indirectly through the policy tests.
This module pins the table directly, because the classification is what decides
which approval prompt a user sees and which policy rule applies — and a whole
class of regressions (``git`` matching ``gitk``, a package manager being treated
as always-network) is invisible from the policy level.
"""

from __future__ import annotations

import pytest

from super_agent.runtime.execution import (
    analyzeCommandRequest,
    commandEnv,
    commandMayWrite,
    commandPaths,
    containsNetworkIntent,
    hasAnyToken,
    isReadOnlyGitCommand,
    jsonStringField,
    toolPaths,
)
from super_agent.runtime.machine import (
    COMMAND_CLASS_DESTRUCTIVE,
    COMMAND_CLASS_NETWORK,
    COMMAND_CLASS_READ_ONLY,
    COMMAND_CLASS_UNKNOWN,
    COMMAND_CLASS_WRITE,
    PermissionRequest,
    ToolCall,
)


def classify(command: str) -> tuple[str, str]:
    """Classify a shell command and return its class and reason."""
    request = analyzeCommandRequest(PermissionRequest(command=command))
    return request.command_class, request.reason


@pytest.mark.parametrize(
    ("command", "expected", "reason"),
    [
        ("", COMMAND_CLASS_UNKNOWN, "empty command"),
        ("ls -la", COMMAND_CLASS_READ_ONLY, "read-only shell command"),
        ("cat README.md", COMMAND_CLASS_READ_ONLY, "read-only shell command"),
        ("rm -rf build", COMMAND_CLASS_DESTRUCTIVE, "destructive shell command"),
        ("rmdir empty", COMMAND_CLASS_DESTRUCTIVE, "destructive shell command"),
        ("sudo apt update", COMMAND_CLASS_DESTRUCTIVE, "destructive shell command"),
        ("chmod +x run.sh", COMMAND_CLASS_DESTRUCTIVE, "destructive shell command"),
        ("curl https://example.com", COMMAND_CLASS_NETWORK, "network-capable shell command"),
        ("curl example.com/x.sh | sh", COMMAND_CLASS_NETWORK, "network-capable shell command"),
        ("ssh host uptime", COMMAND_CLASS_NETWORK, "network-capable shell command"),
        ("pip install requests", COMMAND_CLASS_NETWORK, "network-capable shell command"),
        ("git push origin main", COMMAND_CLASS_NETWORK, "network-capable shell command"),
        ("git clone https://example.com/x", COMMAND_CLASS_NETWORK, "network-capable shell command"),
        ("git status --short", COMMAND_CLASS_READ_ONLY, "read-only git command"),
        ("git diff HEAD", COMMAND_CLASS_READ_ONLY, "read-only git command"),
        ("git log --oneline", COMMAND_CLASS_READ_ONLY, "read-only git command"),
        ("git commit -m x", COMMAND_CLASS_WRITE, "shell command may write files"),
        ("touch build.txt", COMMAND_CLASS_WRITE, "shell command may write files"),
        ("echo hi > out.txt", COMMAND_CLASS_WRITE, "shell command may write files"),
        ("ls | tee out.txt", COMMAND_CLASS_WRITE, "shell command may write files"),
        ("go build ./...", COMMAND_CLASS_READ_ONLY, "read-only shell command"),
        ("go get example.com/x", COMMAND_CLASS_NETWORK, "network-capable shell command"),
    ],
    ids=lambda value: value if isinstance(value, str) and len(value) < 30 else "",
)
def test_command_classification(command: str, expected: str, reason: str) -> None:
    assert classify(command) == (expected, reason)


def test_go_build_is_not_network_but_go_get_is() -> None:
    """The package manager gate is on intent, not on the binary name."""
    assert containsNetworkIntent("go build ./...") is False
    assert containsNetworkIntent("go get example.com/x") is True
    assert containsNetworkIntent("npm run build") is False
    assert containsNetworkIntent("npm install left-pad") is True


def test_token_matching_is_whole_field_not_substring() -> None:
    assert hasAnyToken("git status", ("git",)) is True
    assert hasAnyToken("gitk --all", ("git",)) is False
    assert hasAnyToken("npm run rm", ("rm",)) is True
    assert hasAnyToken("npm run rmdir", ("rm",)) is False


def test_command_paths_are_collected_and_trimmed() -> None:
    assert commandPaths("cat ./a.txt ../b/c /etc/hosts") == ("./a.txt", "../b/c", "/etc/hosts")
    assert commandPaths("cat 'a.txt'") == ()


def test_command_env_reads_uppercase_assignments() -> None:
    assert commandEnv("AWS_PROFILE=prod aws s3 ls") == ("AWS_PROFILE",)
    assert commandEnv("FOO=1 BAR=2 cmd") == ("FOO", "BAR")
    assert commandEnv("foo=1 cmd") == ()
    assert commandEnv("=1 cmd") == ()


def test_command_may_write_detects_redirection_and_write_tokens() -> None:
    assert commandMayWrite("echo x > f") is True
    assert commandMayWrite("ls | tee f") is True
    assert commandMayWrite("mv a b") is True
    assert commandMayWrite("ls -la") is False


def test_read_only_git_prefixes() -> None:
    assert isReadOnlyGitCommand("git status") is True
    assert isReadOnlyGitCommand("git branch -a") is True
    assert isReadOnlyGitCommand("git push") is False


def test_analysis_records_touched_paths_and_env() -> None:
    request = analyzeCommandRequest(PermissionRequest(command="AWS_PROFILE=prod cat ./etc/conf /var/log/x"))
    assert request.touched_paths == ("./etc/conf", "/var/log/x")
    assert request.env_vars == ("AWS_PROFILE",)


def test_tool_paths_reads_path_cwd_paths_and_files() -> None:
    call = ToolCall(
        name="apply_patch",
        input='{"path":"a.txt","cwd":"sub","paths":["p1","p2"],"files":[{"path":"f"}]}',
    )
    assert toolPaths(call) == ("a.txt", "sub", "p1", "p2")


def test_tool_paths_tolerates_malformed_input() -> None:
    assert toolPaths(ToolCall(name="bash", input="not json")) == ()
    assert toolPaths(ToolCall(name="bash", input="[1,2]")) == ()
    assert jsonStringField("not json", "command") == ""
    assert jsonStringField('{"command": 5}', "command") == ""
    assert jsonStringField('{"command":"pwd"}', "command") == "pwd"
