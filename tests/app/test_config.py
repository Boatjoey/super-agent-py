"""Port of ``tests/app/config_test.go``, plus the credential regression case.

The Go tests call ``t.Setenv`` and ``t.Chdir``; here that is ``monkeypatch``, and
the shared fixture in ``tests/conftest.py`` has already pinned ``HOME`` and the
working directory, cleared ``YOLO``/``NO_TOOLS``, and removed every ``*_API_KEY``
from the environment. A test that needs one of those sets it explicitly.

One test has no Go counterpart:
:func:`test_model_uses_resolved_credential_not_placeholder` pins the fix for the
credential bug in ``app/session.go``, which built the adapter from the unresolved
provider map and could therefore send the template placeholder as a bearer token.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

import pytest

from super_agent import llm
from super_agent.app import Flags, LoadConfig, LoadSettingsFile, NewSessionWithExtensions
from super_agent.app.config import Lookup
from super_agent.llm import ProviderConfig
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Message, Model, ModelResponse, StreamChunk, ToolSpec

#: The placeholder a fresh settings.json carries, so the test says why it matters.
PLACEHOLDER: Final[str] = "sk-..."


def lookup(values: Mapping[str, str] | None = None) -> Lookup:
    """Go's ``lookup`` helper: a table lookup that reports "not set" as ``None``."""
    table = {} if values is None else dict(values)

    def get(key: str) -> str | None:
        return table.get(key)

    return get


def writeSettings(home: Path, content: str) -> Path:
    """Write ``settings.json`` under ``home``, creating the directory."""
    directory = home / ".superagent"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "settings.json"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_config_combines_flags_env_and_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"claude","permissions":{"mode":"accept-edits","network":"allow",'
        '"allow_command_prefixes":["git status"]},'
        '"providers":{"claude":{"base_url":"https://claude.test","api_key":"claude-key",'
        '"model":"claude-test"}}}',
    )

    cfg = LoadConfig(Flags(AutoApproveTools=True), lookup({"LLM_PROVIDER": "openai", "NO_TOOLS": "true"}))

    assert cfg.Provider == "claude"
    assert cfg.ModelConfig.BaseURL == "https://claude.test"
    assert cfg.ModelConfig.APIKey == "claude-key"
    assert cfg.ModelConfig.Model == "claude-test"
    assert cfg.NoTools is True
    assert cfg.PermissionMode == "bypass"
    assert list(cfg.PermissionRules.AllowPrefixes) == ["git status"]


def test_load_config_reads_custom_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"deepseek","providers":{"deepseek":{"api_key":"deepseek-key","model":"reasoner"}},'
        '"agent":"reviewer","agents":{"reviewer":{"prompt":"Review only.","permission_mode":"plan"}}}',
    )

    cfg = LoadConfig(Flags(), None)

    assert cfg.Agent == "reviewer"
    assert cfg.Agents["reviewer"].Prompt == "Review only."


def test_load_config_builds_project_and_workspace_from_explicit_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    # With no settings.json the default provider carries only a placeholder key,
    # so the credential has to come from the environment: a provider with no
    # usable credential fails at startup rather than on the first turn.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    selected = tmp_path / "selected"
    selected.mkdir()
    process_cwd = tmp_path / "process"
    process_cwd.mkdir()
    monkeypatch.chdir(process_cwd)

    cfg = LoadConfig(Flags(CWD=str(selected)), None)

    canonical = os.path.realpath(selected)
    assert cfg.Project.Root == canonical
    assert cfg.ConfigRoot == canonical
    workspace = cfg.Workspace
    assert workspace is not None
    assert workspace.GetPrimaryRoot() == canonical
    assert workspace.GetCWD() == canonical
    assert cfg.Sandbox.Workspace == canonical


def test_load_config_reads_lsp_servers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"deepseek","providers":{"deepseek":{"api_key":"deepseek-key"}},'
        '"lsp_servers":{"go":{"command":"gopls","args":["serve"],"extensions":["go"],"language_id":"go"}}}',
    )

    cfg = LoadConfig(Flags(), None)

    assert len(cfg.LSPServers) == 1
    assert cfg.LSPServers[0].Command == "gopls"
    assert cfg.LSPServers[0].LanguageID == "go"


def test_load_config_combines_skills_commands_and_plugins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    (project / "skill").mkdir()
    (project / "skill" / "SKILL.md").write_text("Use focused tests.", encoding="utf-8")
    (project / "plugin").mkdir()
    (project / "plugin" / "plugin.json").write_text(
        '{"commands":{"audit":"Audit $ARGUMENTS"},"hooks":{"after_turn":["go test ./..."]}}',
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    writeSettings(
        home,
        '{"provider":"deepseek","providers":{"deepseek":{"api_key":"deepseek-key"}},'
        '"extensions":{"commands":{"explain":"Explain $ARGUMENTS"},"skills":["skill"],"plugins":["plugin"]}}',
    )

    cfg = LoadConfig(Flags(), None)

    assert cfg.Extensions.Commands["audit"] != ""
    assert cfg.Extensions.Commands["explain"] != ""
    assert "Use focused tests." in cfg.Extensions.SkillPrompt
    assert len(cfg.Extensions.Hooks["after_turn"]) == 1


def test_load_config_uses_settings_permission_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"openai","permissions":{"mode":"plan"},"providers":{"openai":{"api_key":"key","model":"model"}}}',
    )

    cfg = LoadConfig(Flags(), lookup())

    assert cfg.PermissionMode == "plan"


def test_load_config_rejects_invalid_permission_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"openai","permissions":{"mode":"root"},"providers":{"openai":{"api_key":"key","model":"model"}}}',
    )

    with pytest.raises(ValueError) as failure:
        LoadConfig(Flags(), lookup())

    assert "invalid permission mode: root" in str(failure.value)


def test_load_config_creates_default_settings_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    cfg = LoadConfig(Flags(), lookup({"DEEPSEEK_API_KEY": "env-key"}))

    assert cfg.Provider == "deepseek"
    assert cfg.ModelConfig.Model == "deepseek-reasoner"
    assert cfg.PermissionMode == "ask"
    assert cfg.Sandbox.Mode == "strict"
    assert cfg.Sandbox.AllowNetwork is False


def test_load_config_maps_sandbox_and_network_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"openai","permissions":{"network":"allow"},'
        '"sandbox":{"mode":"off","cpu_seconds":9,"memory_mb":64,"max_processes":7,"max_open_files":11},'
        '"providers":{"openai":{"api_key":"key","model":"model"}}}',
    )

    cfg = LoadConfig(Flags(), lookup())

    assert cfg.Sandbox.Mode == "off"
    assert cfg.Sandbox.AllowNetwork is True
    assert cfg.Sandbox.CPUSeconds == 9
    assert cfg.Sandbox.MemoryBytes == 64 << 20
    assert cfg.Sandbox.MaxProcesses == 7
    assert cfg.Sandbox.MaxOpenFiles == 11


def test_load_config_rejects_invalid_sandbox_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"openai","sandbox":{"mode":"maybe"},"providers":{"openai":{"api_key":"key","model":"model"}}}',
    )

    with pytest.raises(ValueError) as failure:
        LoadConfig(Flags(), lookup())

    assert "invalid sandbox mode: maybe" in str(failure.value)


def test_load_config_maps_mcp_servers_in_name_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"openai","mcp_servers":{'
        '"z":{"command":"z-server"},'
        '"a":{"command":"a-server","args":["--stdio"],"env":{"TOKEN":"explicit"},"cwd":"nested",'
        '"connect_timeout_seconds":3,"call_timeout_seconds":4}},'
        '"providers":{"openai":{"api_key":"key","model":"model"}}}',
    )

    cfg = LoadConfig(Flags(), lookup())

    assert [server.Name for server in cfg.MCPServers] == ["a", "z"]
    server = cfg.MCPServers[0]
    assert server.Command == "a-server"
    assert list(server.Args) == ["--stdio"]
    assert server.Env["TOKEN"] == "explicit"
    assert os.path.isabs(server.CWD)
    assert server.ConnectTimeout == 3.0
    assert server.CallTimeout == 4.0


def test_load_settings_file_creates_template_when_missing(tmp_path: Path) -> None:
    path = tmp_path / ".superagent" / "settings.json"

    settings = LoadSettingsFile(str(path))

    content = path.read_text(encoding="utf-8")
    assert content != ""
    assert settings.Provider == "deepseek"
    assert settings.Providers["deepseek"].Model == "deepseek-reasoner"
    assert settings.Permissions.Mode == "ask"
    assert settings.Permissions.Network == "deny"
    assert '"permissions"' in content
    # The disk format is a contract with the Go implementation: two-space indent,
    # a trailing newline, and a file nobody else may read.
    assert content.endswith("\n")
    assert '\n  "provider"' in content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_load_settings_file_does_not_overwrite_existing_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    existing = '{"provider":"openai","providers":{"openai":{"api_key":"keep","model":"custom"}}}'
    path.write_text(existing, encoding="utf-8")

    settings = LoadSettingsFile(str(path))

    assert path.read_text(encoding="utf-8") == existing
    assert settings.Provider == "openai"
    assert settings.Providers["openai"].APIKey == "keep"


def test_yolo_env_does_not_override_explicit_approval_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    cfg = LoadConfig(Flags(PermissionMode="ask"), lookup({"YOLO": "true", "DEEPSEEK_API_KEY": "env-key"}))

    assert cfg.PermissionMode == "ask", "an explicit --approval-mode flag must win over YOLO"


def test_yolo_env_enables_bypass_without_explicit_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    cfg = LoadConfig(Flags(), lookup({"YOLO": "true", "DEEPSEEK_API_KEY": "env-key"}))
    assert cfg.PermissionMode == "bypass"

    # Only the exact string counts: True, 1, and yes must not enable bypass.
    cfg = LoadConfig(Flags(), lookup({"YOLO": "True", "DEEPSEEK_API_KEY": "env-key"}))
    assert cfg.PermissionMode == "ask"


class _RecordingModel:
    """A model adapter that records the configuration it was built from."""

    __slots__ = ("config",)

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    async def Next(
        self,
        ctx: RunContext,
        messages: list[Message],
        tools: list[ToolSpec],
        on_stream_chunk: Callable[[StreamChunk], None],
    ) -> ModelResponse:
        return ModelResponse()


@pytest.mark.asyncio
async def test_model_uses_resolved_credential_not_placeholder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The session must build the adapter from the resolved credential.

    ``providers.deepseek.api_key`` still holds the template placeholder when the
    real credential comes from the environment. Building the model from that map
    entry — which is what the Go implementation does — sends ``sk-...`` as a
    bearer token.
    """
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"deepseek","providers":{"deepseek":{"api_key":"'
        + PLACEHOLDER
        + '","model":"deepseek-reasoner"}}}',
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "resolved-key")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    cfg = LoadConfig(Flags(), None)
    assert cfg.ModelConfig.APIKey == "resolved-key"
    assert cfg.ProviderConfigs["deepseek"].APIKey == PLACEHOLDER
    cfg.NoTools = True

    built: list[ProviderConfig] = []

    def build(provider: str, config: ProviderConfig) -> Model:
        built.append(config)
        return _RecordingModel(config)

    monkeypatch.setattr(llm, "NewModel", build)

    session, _mcp, _agents = await NewSessionWithExtensions(cfg)
    try:
        assert [config.APIKey for config in built] == ["resolved-key"]
    finally:
        await session.Close()
