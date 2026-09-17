"""Configuration loading and resolution, plus the credential regression case.

The shared fixture in ``tests/conftest.py`` has already pinned ``HOME`` and the
working directory, cleared ``YOLO``/``NO_TOOLS``, and removed every ``*_API_KEY``
from the environment; a test that needs one of those sets it explicitly with
``monkeypatch``.

:func:`test_model_uses_resolved_credential_not_placeholder` pins the fix for the
credential bug in ``app/session.py``, which built the adapter from the unresolved
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
from super_agent.app import Flags, load_config, load_settings_file, new_session_with_extensions
from super_agent.app.config import Lookup
from super_agent.llm import ProviderConfig
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import Message, Model, ModelResponse, StreamChunk, ToolSpec

#: The placeholder a fresh settings.json carries, so the test says why it matters.
PLACEHOLDER: Final[str] = "sk-..."


def lookup(values: Mapping[str, str] | None = None) -> Lookup:
    """A table lookup that reports "not set" as ``None``."""
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

    cfg = load_config(Flags(auto_approve_tools=True), lookup({"LLM_PROVIDER": "openai", "NO_TOOLS": "true"}))

    assert cfg.provider == "claude"
    assert cfg.model_config.base_url == "https://claude.test"
    assert cfg.model_config.api_key == "claude-key"
    assert cfg.model_config.model == "claude-test"
    assert cfg.no_tools is True
    assert cfg.permission_mode == "bypass"
    assert list(cfg.permission_rules.allow_prefixes) == ["git status"]


def test_load_config_reads_custom_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"deepseek","providers":{"deepseek":{"api_key":"deepseek-key","model":"reasoner"}},'
        '"agent":"reviewer","agents":{"reviewer":{"prompt":"Review only.","permission_mode":"plan"}}}',
    )

    cfg = load_config(Flags(), None)

    assert cfg.agent == "reviewer"
    assert cfg.agents["reviewer"].prompt == "Review only."


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

    cfg = load_config(Flags(cwd=str(selected)), None)

    canonical = os.path.realpath(selected)
    assert cfg.project.root == canonical
    assert cfg.config_root == canonical
    workspace = cfg.workspace
    assert workspace is not None
    assert workspace.get_primary_root() == canonical
    assert workspace.get_cwd() == canonical
    assert cfg.sandbox.workspace == canonical


def test_load_config_reads_lsp_servers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"deepseek","providers":{"deepseek":{"api_key":"deepseek-key"}},'
        '"lsp_servers":{"go":{"command":"gopls","args":["serve"],"extensions":["go"],"language_id":"go"}}}',
    )

    cfg = load_config(Flags(), None)

    assert len(cfg.lsp_servers) == 1
    assert cfg.lsp_servers[0].command == "gopls"
    assert cfg.lsp_servers[0].language_id == "go"


def test_load_config_combines_skills_commands_and_plugins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    project = tmp_path / "project"
    project.mkdir()
    (project / "skill").mkdir()
    (project / "skill" / "SKILL.md").write_text("Use focused tests.", encoding="utf-8")
    (project / "plugin").mkdir()
    (project / "plugin" / "plugin.json").write_text(
        '{"commands":{"audit":"Audit $ARGUMENTS"},"hooks":{"after_turn":["uv run pytest -q"]}}',
        encoding="utf-8",
    )
    monkeypatch.chdir(project)
    writeSettings(
        home,
        '{"provider":"deepseek","providers":{"deepseek":{"api_key":"deepseek-key"}},'
        '"extensions":{"commands":{"explain":"Explain $ARGUMENTS"},"skills":["skill"],"plugins":["plugin"]}}',
    )

    cfg = load_config(Flags(), None)

    assert cfg.extensions.commands["audit"] != ""
    assert cfg.extensions.commands["explain"] != ""
    assert "Use focused tests." in cfg.extensions.skill_prompt
    assert len(cfg.extensions.hooks["after_turn"]) == 1


def test_load_config_uses_settings_permission_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"openai","permissions":{"mode":"plan"},"providers":{"openai":{"api_key":"key","model":"model"}}}',
    )

    cfg = load_config(Flags(), lookup())

    assert cfg.permission_mode == "plan"


def test_load_config_rejects_invalid_permission_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"openai","permissions":{"mode":"root"},"providers":{"openai":{"api_key":"key","model":"model"}}}',
    )

    with pytest.raises(ValueError) as failure:
        load_config(Flags(), lookup())

    assert "invalid permission mode: root" in str(failure.value)


def test_load_config_creates_default_settings_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    cfg = load_config(Flags(), lookup({"DEEPSEEK_API_KEY": "env-key"}))

    assert cfg.provider == "deepseek"
    assert cfg.model_config.model == "deepseek-reasoner"
    assert cfg.permission_mode == "ask"
    assert cfg.sandbox.mode == "strict"
    assert cfg.sandbox.allow_network is False


def test_load_config_maps_sandbox_and_network_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"openai","permissions":{"network":"allow"},'
        '"sandbox":{"mode":"off","cpu_seconds":9,"memory_mb":64,"max_processes":7,"max_open_files":11},'
        '"providers":{"openai":{"api_key":"key","model":"model"}}}',
    )

    cfg = load_config(Flags(), lookup())

    assert cfg.sandbox.mode == "off"
    assert cfg.sandbox.allow_network is True
    assert cfg.sandbox.cpu_seconds == 9
    assert cfg.sandbox.memory_bytes == 64 << 20
    assert cfg.sandbox.max_processes == 7
    assert cfg.sandbox.max_open_files == 11


def test_load_config_rejects_invalid_sandbox_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    writeSettings(
        home,
        '{"provider":"openai","sandbox":{"mode":"maybe"},"providers":{"openai":{"api_key":"key","model":"model"}}}',
    )

    with pytest.raises(ValueError) as failure:
        load_config(Flags(), lookup())

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

    cfg = load_config(Flags(), lookup())

    assert [server.name for server in cfg.mcp_servers] == ["a", "z"]
    server = cfg.mcp_servers[0]
    assert server.command == "a-server"
    assert list(server.args) == ["--stdio"]
    assert server.env["TOKEN"] == "explicit"
    assert os.path.isabs(server.cwd)
    assert server.connect_timeout == 3.0
    assert server.call_timeout == 4.0


def test_load_settings_file_creates_template_when_missing(tmp_path: Path) -> None:
    path = tmp_path / ".superagent" / "settings.json"

    settings = load_settings_file(str(path))

    content = path.read_text(encoding="utf-8")
    assert content != ""
    assert settings.provider == "deepseek"
    assert settings.providers["deepseek"].model == "deepseek-reasoner"
    assert settings.permissions.mode == "ask"
    assert settings.permissions.network == "deny"
    assert '"permissions"' in content
    # The disk format is a contract: two-space indent,
    # a trailing newline, and a file nobody else may read.
    assert content.endswith("\n")
    assert '\n  "provider"' in content
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_load_settings_file_does_not_overwrite_existing_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    existing = '{"provider":"openai","providers":{"openai":{"api_key":"keep","model":"custom"}}}'
    path.write_text(existing, encoding="utf-8")

    settings = load_settings_file(str(path))

    assert path.read_text(encoding="utf-8") == existing
    assert settings.provider == "openai"
    assert settings.providers["openai"].api_key == "keep"


def test_yolo_env_does_not_override_explicit_approval_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    cfg = load_config(Flags(permission_mode="ask"), lookup({"YOLO": "true", "DEEPSEEK_API_KEY": "env-key"}))

    assert cfg.permission_mode == "ask", "an explicit --approval-mode flag must win over YOLO"


def test_yolo_env_enables_bypass_without_explicit_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    cfg = load_config(Flags(), lookup({"YOLO": "true", "DEEPSEEK_API_KEY": "env-key"}))
    assert cfg.permission_mode == "bypass"

    # Only the exact string counts: True, 1, and yes must not enable bypass.
    cfg = load_config(Flags(), lookup({"YOLO": "True", "DEEPSEEK_API_KEY": "env-key"}))
    assert cfg.permission_mode == "ask"


class _RecordingModel:
    """A model adapter that records the configuration it was built from."""

    __slots__ = ("config",)

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    async def next(
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
    entry sends ``sk-...`` as a bearer token.
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

    cfg = load_config(Flags(), None)
    assert cfg.model_config.api_key == "resolved-key"
    assert cfg.provider_configs["deepseek"].api_key == PLACEHOLDER
    cfg.no_tools = True

    built: list[ProviderConfig] = []

    def build(provider: str, config: ProviderConfig) -> Model:
        built.append(config)
        return _RecordingModel(config)

    monkeypatch.setattr(llm, "new_model", build)

    session, _mcp, _agents = await new_session_with_extensions(cfg)
    try:
        assert [config.api_key for config in built] == ["resolved-key"]
    finally:
        await session.close()
