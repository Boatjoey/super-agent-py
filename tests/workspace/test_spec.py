"""Workspace activation and the one-time canonical upgrade of legacy specs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from super_agent import workspace
from super_agent.runtime.session import (
    WORKSPACE_ACCESS_READ,
    WORKSPACE_ACCESS_READ_WRITE,
    WorkspaceRootSpec,
    WorkspaceSpec,
)


def test_workspace_spec_round_trips_through_validated_runtime(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    read_only = tmp_path / "readonly"
    for directory in (primary, read_only):
        directory.mkdir()
    context = workspace.new_context(
        str(primary),
        str(primary),
        [
            workspace.Root(path=str(primary), access=workspace.ACCESS_READ_WRITE),
            workspace.Root(path=str(read_only), access=workspace.ACCESS_READ),
        ],
    )
    runtime_workspace = workspace.new(context)

    spec = runtime_workspace.spec()
    runtime_workspace.activate(spec)

    assert runtime_workspace.get_cwd() == spec.cwd
    assert runtime_workspace.can_write(str(primary / "new"))
    assert runtime_workspace.can_read(str(read_only / "file"))
    assert not runtime_workspace.can_write(str(read_only / "file"))


def test_workspace_activation_rejects_invalid_saved_paths_without_changing_current_context(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    context = workspace.new_default_context(str(current))
    runtime_workspace = workspace.new(context)

    missing = tmp_path / "missing"
    invalid = WorkspaceSpec(
        primary_root=str(missing),
        cwd=str(missing),
        roots=(WorkspaceRootSpec(path=str(missing), access=WORKSPACE_ACCESS_READ_WRITE),),
    )
    with pytest.raises((OSError, ValueError)):
        runtime_workspace.activate(invalid)
    assert runtime_workspace.get_cwd() == context.get_cwd()

    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()
    outside = WorkspaceSpec(
        primary_root=str(current),
        cwd=str(outside_cwd),
        roots=(WorkspaceRootSpec(path=str(current), access=WORKSPACE_ACCESS_READ_WRITE),),
    )
    with pytest.raises(ValueError, match="cwd is not readable"):
        runtime_workspace.activate(outside)


def test_workspace_activation_rejects_missing_additional_root(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()
    missing = tmp_path / "missing"
    context = workspace.new_default_context(str(primary))
    runtime_workspace = workspace.new(context)

    spec = WorkspaceSpec(
        primary_root=str(primary),
        cwd=str(primary),
        roots=(
            WorkspaceRootSpec(path=str(primary), access=WORKSPACE_ACCESS_READ_WRITE),
            WorkspaceRootSpec(path=str(missing), access=WORKSPACE_ACCESS_READ),
        ),
    )
    with pytest.raises((OSError, ValueError)):
        runtime_workspace.activate(spec)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks commonly need elevated privileges on Windows")
def test_workspace_activation_rejects_root_replaced_by_escaping_symlink(tmp_path: Path) -> None:
    saved_root = tmp_path / "project"
    saved_root.mkdir()
    saved_context = workspace.new_default_context(str(saved_root))
    spec = workspace.new(saved_context).spec()

    os.rmdir(saved_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, saved_root)

    current = tmp_path / "current"
    current.mkdir()
    current_context = workspace.new_default_context(str(current))
    with pytest.raises(ValueError, match="resolves to a different path"):
        workspace.new(current_context).activate(spec)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks commonly need elevated privileges on Windows")
def test_workspace_canonicalize_upgrades_legacy_non_canonical_path(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    other = tmp_path / "other"
    other.mkdir()
    context = workspace.new_default_context(str(other))
    runtime_workspace = workspace.new(context)

    # Legacy metadata may hold a path that is not itself canonical, such as a
    # symlinked cwd. Canonicalize upgrades it; Validate then accepts the result.
    legacy = WorkspaceSpec(
        primary_root=str(link),
        cwd=str(link),
        roots=(WorkspaceRootSpec(path=str(link), access=WORKSPACE_ACCESS_READ_WRITE),),
    )
    upgraded = runtime_workspace.canonicalize(legacy)
    canonical = os.path.realpath(str(link))

    assert upgraded.primary_root == canonical
    assert canonical == upgraded.cwd
    assert len(upgraded.roots) == 1
    assert upgraded.roots[0].path == canonical

    again = runtime_workspace.canonicalize(upgraded)
    assert upgraded == again

    runtime_workspace.validate(upgraded)
