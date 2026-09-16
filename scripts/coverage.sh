#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${project_root}"

# Coverage is collected over the production package, not just the modules the
# tests happen to import, so untested code shows up as uncovered. Extra
# arguments are forwarded to pytest.
if [[ $# -gt 0 ]]; then
    uv run pytest --cov=super_agent --cov-report=term-missing "$@"
else
    uv run pytest --cov=super_agent --cov-report=term-missing
fi
