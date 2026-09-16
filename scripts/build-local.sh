#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_dir="${SUPER_AGENT_INSTALL_DIR:-/usr/local/bin}"
binary_name="${SUPER_AGENT_BINARY_NAME:-super-agent}"
venv_dir="${SUPER_AGENT_VENV:-${XDG_DATA_HOME:-$HOME/.local/share}/super-agent-py/venv}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

# The console script is installed into a dedicated virtual environment and a
# small launcher is placed on PATH. The launcher forwards every argument and
# exit code unchanged, so `super-agent -h` behaves exactly like the console
# script.
uv build --wheel --out-dir "$tmp_dir" "$repo_root" >/dev/null
wheel="$(find "$tmp_dir" -maxdepth 1 -name '*.whl' -print -quit)"
if [[ -z "$wheel" ]]; then
    printf 'build produced no wheel\n' >&2
    exit 1
fi

uv venv --allow-existing "$venv_dir" >/dev/null
uv pip install --python "$venv_dir/bin/python" --reinstall --quiet "$wheel"

launcher="$tmp_dir/$binary_name"
cat >"$launcher" <<EOF
#!/bin/sh
exec "$venv_dir/bin/$binary_name" "\$@"
EOF
chmod 0755 "$launcher"

# The install directory may not exist yet when SUPER_AGENT_INSTALL_DIR names a
# fresh path for automation. Creating it here keeps that documented usage working
# instead of falling through to a sudo prompt.
if [[ ! -d "$install_dir" ]] && ! mkdir -p "$install_dir" 2>/dev/null; then
    if command -v sudo >/dev/null 2>&1; then
        sudo mkdir -p "$install_dir"
    else
        printf 'cannot create %s; rerun with sudo or set SUPER_AGENT_INSTALL_DIR\n' "$install_dir" >&2
        exit 1
    fi
fi

if [[ -w "$install_dir" ]]; then
    install -m 0755 "$launcher" "$install_dir/$binary_name"
elif command -v sudo >/dev/null 2>&1; then
    sudo install -m 0755 "$launcher" "$install_dir/$binary_name"
else
    printf 'cannot write %s; rerun with sudo or set SUPER_AGENT_INSTALL_DIR\n' "$install_dir" >&2
    exit 1
fi

printf 'installed %s\n' "$install_dir/$binary_name"
