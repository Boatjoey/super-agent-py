"""Layered-instruction loading and session construction.

The layered-instruction tests are pure function calls. The session tests build a
real session, which means building a real model: the OpenAI SDK the Python
adapter wraps refuses to construct without a credential, so those tests supply
``ModelConfig`` explicitly. Nothing in them reaches the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from super_agent.app import (
    Config,
    Extensions,
    instructions,
    load_project_instructions,
    new_session,
    new_session_with_extensions,
)
from super_agent.app.instructions import MAX_FILE_SIZE
from super_agent.errors import JoinedError
from super_agent.llm import ProviderConfig
from super_agent.runtime import PERMISSION_MODE_ASK, PERMISSION_MODE_PLAN, ROLE_SYSTEM
from super_agent.workspace import Context, new_default_context

#: A credential the adapters accept. No request is ever made with it.
TEST_KEY = "test-key"

#: The message a custom profile appends, so the switch is observable.
PLAN_MARKER = "Active agent profile: plan"


def mustWorkspaceContext(root: Path) -> Context:
    """A workspace whose primary root and cwd are both ``root``."""
    return new_default_context(str(root))


def sessionConfig(root: Path, *, provider: str = "deepseek") -> Config:
    """A minimal session configuration: no tools, no network, and a credential."""
    return Config(
        provider=provider,
        no_tools=True,
        permission_mode=PERMISSION_MODE_ASK,
        model_config=ProviderConfig(api_key=TEST_KEY),
        workspace=mustWorkspaceContext(root),
        config_root=str(root),
    )


def test_load_project_instructions_merges_root_to_leaf(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "root"
    (root / ".git").mkdir(parents=True)
    nested = root / "pkg" / "feature"
    nested.mkdir(parents=True)
    (root / "AGENTS.md").write_text("root rules\n", encoding="utf-8")
    (root / "pkg" / "AGENTS.md").write_text("pkg rules\n", encoding="utf-8")
    (nested / "AGENTS.md").write_text("leaf rules\n", encoding="utf-8")

    got = load_project_instructions(str(nested))

    assert got == "root rules\n\npkg rules\n\nleaf rules"


def test_load_starts_at_git_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    parent = tmp_path / "parent"
    repo = parent / "repo"
    nested = repo / "pkg"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    (parent / "AGENTS.md").write_text("outside", encoding="utf-8")
    (repo / "AGENTS.md").write_text("repo", encoding="utf-8")
    (nested / "AGENTS.md").write_text("nested", encoding="utf-8")

    got = load_project_instructions(str(nested))

    assert got == "repo\n\nnested"


def test_load_includes_user_spec_before_project_instructions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    (home / ".superagent").mkdir(parents=True)
    (home / ".superagent" / "AGENTS.md").write_text("user spec", encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("project rules", encoding="utf-8")

    bundle = instructions.load(str(project))

    assert bundle.content == "user spec\n\nproject rules"
    assert [source.kind for source in bundle.sources] == ["user-spec", "project"]


def test_load_uses_claude_when_same_directory_agents_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "CLAUDE.md").write_text("root claude", encoding="utf-8")
    (nested / "CLAUDE.md").write_text("nested claude", encoding="utf-8")
    (nested / "AGENTS.md").write_text("nested agents", encoding="utf-8")

    bundle = instructions.load(str(nested))

    assert bundle.content == "root claude\n\nnested agents"
    assert [source.kind for source in bundle.sources] == ["claude-compat", "project"]


def test_load_rejects_oversized_instruction_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    directory = tmp_path / "dir"
    directory.mkdir()
    (directory / "AGENTS.md").write_text("x" * (MAX_FILE_SIZE + 1), encoding="utf-8")

    with pytest.raises(ValueError) as failure:
        instructions.load(str(directory))

    assert "too large" in str(failure.value)
    assert "AGENTS.md" in str(failure.value)


def test_load_project_instructions_allows_missing_agents_md(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    empty = tmp_path / "empty"
    empty.mkdir()

    assert load_project_instructions(str(empty)) == ""


@pytest.mark.asyncio
async def test_new_session_injects_system_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    directory = tmp_path / "project"
    directory.mkdir()
    monkeypatch.chdir(directory)

    session = await new_session(sessionConfig(directory))
    try:
        messages = session.snapshot().messages
        assert len(messages) == 1
        assert messages[0].role == ROLE_SYSTEM
        assert messages[0].content != ""
        assert messages[0].content != "# Rules\n\n- keep tests focused"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_agent_controller_switches_plan_and_build_profiles(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    directory = tmp_path / "project"
    directory.mkdir()
    monkeypatch.chdir(directory)

    session, _mcp, agents = await new_session_with_extensions(sessionConfig(directory))
    try:
        await agents.use("plan")
        assert session.permission_mode() == PERMISSION_MODE_PLAN
        assert PLAN_MARKER in session.snapshot().messages[0].content

        await agents.use("build")
        assert agents.active_profile().name == "build"
        assert session.permission_mode() == PERMISSION_MODE_ASK
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_session_loads_instructions_from_config_root_not_workspace_access_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    configRoot = tmp_path / "config"
    configRoot.mkdir()
    accessRoot = tmp_path / "access"
    accessRoot.mkdir()
    (configRoot / "AGENTS.md").write_text("trusted project instructions", encoding="utf-8")
    (accessRoot / "AGENTS.md").write_text("untrusted access-root instructions", encoding="utf-8")

    session, _mcp, _agents = await new_session_with_extensions(
        Config(
            provider="deepseek",
            no_tools=True,
            permission_mode=PERMISSION_MODE_ASK,
            model_config=ProviderConfig(api_key=TEST_KEY),
            workspace=mustWorkspaceContext(accessRoot),
            config_root=str(configRoot),
        )
    )
    try:
        content = session.snapshot().messages[0].content
        assert "trusted project instructions" in content
        assert "untrusted access-root instructions" not in content
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_configured_hooks_require_tools(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    directory = tmp_path / "project"
    directory.mkdir()
    monkeypatch.chdir(directory)
    cfg = sessionConfig(directory)
    cfg.extensions = Extensions(hooks={"startup": ("true",)})

    with pytest.raises(JoinedError) as failure:
        await new_session_with_extensions(cfg)

    assert "hooks require tools" in str(failure.value)


def test_load_empty_agents_falls_back_to_claude_md(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    directory = tmp_path / "dir"
    directory.mkdir()
    (directory / "AGENTS.md").write_text("   \n", encoding="utf-8")
    (directory / "CLAUDE.md").write_text("# Project Guidance\n\nrules from claude", encoding="utf-8")
    monkeypatch.chdir(directory)

    bundle = instructions.load(str(directory))

    assert "rules from claude" in bundle.content
