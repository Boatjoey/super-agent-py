"""Port of ``tests/build/build_local_test.go``.

There is no static binary to link the way ``go build`` produces one, so the
script installs the console script into a dedicated virtual environment and puts
a launcher on ``PATH``. The assertions are the Go test's: the launcher exists, it
is executable, and ``-h`` succeeds without claiming that ``-yolo`` defaults to
true.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: The text Go prints for a flag whose default is not the zero value. ``-yolo``
#: defaults to false, so its appearance would mean the flag had been inverted.
FORBIDDEN_HELP_TEXT = "Auto-approve tool execution (default true)"


def test_build_local_script_produces_runnable_binary(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    installDir = tmp_path / "bin"
    installDir.mkdir()

    result = subprocess.run(
        ["./scripts/build-local.sh"],
        cwd=repo,
        env={**os.environ, "SUPER_AGENT_INSTALL_DIR": str(installDir)},
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert result.returncode == 0, f"build-local failed:\n{result.stdout}{result.stderr}"

    binary = installDir / "super-agent"
    assert binary.exists()
    assert os.access(binary, os.X_OK)

    helpResult = subprocess.run([str(binary), "-h"], capture_output=True, text=True, check=False)
    assert helpResult.returncode == 0, f"binary -h failed:\n{helpResult.stdout}{helpResult.stderr}"
    assert FORBIDDEN_HELP_TEXT not in helpResult.stdout + helpResult.stderr
