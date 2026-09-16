"""Workspace activation and the one-time canonical upgrade of legacy specs.

Ported from ``tests/workspace/spec_test.go``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from super_agent import workspace
from super_agent.runtime.session import (
    WorkspaceAccessRead,
    WorkspaceAccessReadWrite,
    WorkspaceRootSpec,
    WorkspaceSpec,
)


def test_workspace_spec_round_trips_through_validated_runtime(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    read_only = tmp_path / "readonly"
    for directory in (primary, read_only):
        directory.mkdir()
    context = workspace.NewContext(
        str(primary),
        str(primary),
        [
            workspace.Root(Path=str(primary), Access=workspace.AccessReadWrite),
            workspace.Root(Path=str(read_only), Access=workspace.AccessRead),
        ],
    )
    runtime_workspace = workspace.New(context)

    spec = runtime_workspace.Spec()
    runtime_workspace.Activate(spec)

    assert runtime_workspace.GetCWD() == spec.CWD
    assert runtime_workspace.CanWrite(str(primary / "new"))
    assert runtime_workspace.CanRead(str(read_only / "file"))
    assert not runtime_workspace.CanWrite(str(read_only / "file"))


def test_workspace_activation_rejects_invalid_saved_paths_without_changing_current_context(
    tmp_path: Path,
) -> None:
    current = tmp_path / "current"
    current.mkdir()
    context = workspace.NewDefaultContext(str(current))
    runtime_workspace = workspace.New(context)

    missing = tmp_path / "missing"
    invalid = WorkspaceSpec(
        PrimaryRoot=str(missing),
        CWD=str(missing),
        Roots=(WorkspaceRootSpec(Path=str(missing), Access=WorkspaceAccessReadWrite),),
    )
    with pytest.raises((OSError, ValueError)):
        runtime_workspace.Activate(invalid)
    assert runtime_workspace.GetCWD() == context.GetCWD()

    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()
    outside = WorkspaceSpec(
        PrimaryRoot=str(current),
        CWD=str(outside_cwd),
        Roots=(WorkspaceRootSpec(Path=str(current), Access=WorkspaceAccessReadWrite),),
    )
    with pytest.raises(ValueError, match="cwd is not readable"):
        runtime_workspace.Activate(outside)


def test_workspace_activation_rejects_missing_additional_root(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    primary.mkdir()
    missing = tmp_path / "missing"
    context = workspace.NewDefaultContext(str(primary))
    runtime_workspace = workspace.New(context)

    spec = WorkspaceSpec(
        PrimaryRoot=str(primary),
        CWD=str(primary),
        Roots=(
            WorkspaceRootSpec(Path=str(primary), Access=WorkspaceAccessReadWrite),
            WorkspaceRootSpec(Path=str(missing), Access=WorkspaceAccessRead),
        ),
    )
    with pytest.raises((OSError, ValueError)):
        runtime_workspace.Activate(spec)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks commonly need elevated privileges on Windows")
def test_workspace_activation_rejects_root_replaced_by_escaping_symlink(tmp_path: Path) -> None:
    saved_root = tmp_path / "project"
    saved_root.mkdir()
    saved_context = workspace.NewDefaultContext(str(saved_root))
    spec = workspace.New(saved_context).Spec()

    os.rmdir(saved_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, saved_root)

    current = tmp_path / "current"
    current.mkdir()
    current_context = workspace.NewDefaultContext(str(current))
    with pytest.raises(ValueError, match="resolves to a different path"):
        workspace.New(current_context).Activate(spec)


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks commonly need elevated privileges on Windows")
def test_workspace_canonicalize_upgrades_legacy_non_canonical_path(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    other = tmp_path / "other"
    other.mkdir()
    context = workspace.NewDefaultContext(str(other))
    runtime_workspace = workspace.New(context)

    # Legacy metadata may hold a path that is not itself canonical, such as a
    # symlinked cwd. Canonicalize upgrades it; Validate then accepts the result.
    legacy = WorkspaceSpec(
        PrimaryRoot=str(link),
        CWD=str(link),
        Roots=(WorkspaceRootSpec(Path=str(link), Access=WorkspaceAccessReadWrite),),
    )
    upgraded = runtime_workspace.Canonicalize(legacy)
    canonical = os.path.realpath(str(link))

    assert upgraded.PrimaryRoot == canonical
    assert canonical == upgraded.CWD
    assert len(upgraded.Roots) == 1
    assert upgraded.Roots[0].Path == canonical

    again = runtime_workspace.Canonicalize(upgraded)
    assert upgraded == again

    runtime_workspace.Validate(upgraded)
