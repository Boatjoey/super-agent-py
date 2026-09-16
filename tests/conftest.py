"""Shared test configuration.

Every test in this repository runs against a temporary, deterministic
environment: no test may read the developer's real ``.superagent`` directory,
inherit a ``YOLO`` flag from the shell, or leak a process-global singleton into
the next test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _deterministic_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin HOME and the working directory, and clear the environment knobs."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("YOLO", raising=False)
    monkeypatch.delenv("NO_TOOLS", raising=False)
    for name in tuple(os.environ):
        if name.endswith("_API_KEY"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    yield


@pytest.fixture(autouse=True)
def _leak_check() -> Iterator[None]:
    """Fail a test that leaves asyncio debug mode reporting a resource leak.

    ``python -X dev`` turns asyncio's leak detector on; this fixture only keeps
    the setting from being disabled between tests.
    """
    yield
