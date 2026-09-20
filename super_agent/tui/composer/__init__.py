"""The composer feature.

The package surface, re-exported so callers keep writing
``composer.Model`` and ``composer.SUBMIT``.
"""

from __future__ import annotations

from super_agent.tui.composer.model import (
    Command as Command,
    Intent as Intent,
    IntentKind as IntentKind,
    Model as Model,
    Styles as Styles,
    new as new,
    normalize_commands as normalize_commands,
)
from super_agent.tui.composer.widget import Composer as Composer

#: The package-level intent constants.
NO_INTENT = IntentKind.NO_INTENT
SUBMIT = IntentKind.SUBMIT
QUEUE = IntentKind.QUEUE
STEER = IntentKind.STEER
CLEAR = IntentKind.CLEAR

__all__ = [
    "CLEAR",
    "NO_INTENT",
    "QUEUE",
    "STEER",
    "SUBMIT",
    "Command",
    "Composer",
    "Intent",
    "IntentKind",
    "Model",
    "Styles",
    "new",
    "normalize_commands",
]
