"""Environment scrubbing for tool subprocesses.

Ported from ``tools/env.go``. The parent environment is not inherited wholesale
by child processes: a tool call as ordinary as ``printenv`` would otherwise
hand every API key in the environment straight to the model.
"""

from __future__ import annotations

import os
from typing import Final

#: Name fragments that mark a variable as a credential.
secret_env_markers: Final[tuple[str, ...]] = (
    "API_KEY",
    "APIKEY",
    "AUTH_TOKEN",
    "ACCESS_TOKEN",
    "SESSION_TOKEN",
    # Bare TOKEN covers the common one-off names (GITHUB_TOKEN, NPM_TOKEN,
    # HF_TOKEN, GITLAB_TOKEN, …) that do not spell out auth/access.
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE_KEY",
    # AWS access keys have no secret-shaped name apart from the key id.
    "AWS_ACCESS_KEY_ID",
    # Connection URLs commonly embed credentials inline.
    "DATABASE_URL",
    "POSTGRES_URL",
    "MYSQL_URL",
    "MONGODB_URI",
    "MONGO_URL",
    "REDIS_URL",
)


def child_env() -> dict[str, str]:
    """The environment for tool subprocesses: credentials removed.

    A denylist rather than an allowlist on purpose. Tool subprocesses
    legitimately need a wide range of variables (PATH, HOME, proxy settings, Go
    and git configuration), and an allowlist narrow enough to be safe would
    break builds. The names that must never leak are a much smaller and more
    stable set.
    """
    return {name: value for name, value in os.environ.items() if not is_secret_env_name(name)}


def is_secret_env_name(name: str) -> bool:
    """Whether ``name`` looks like a credential variable."""
    upper = name.upper()
    return any(marker in upper for marker in secret_env_markers)
