"""The composer feature.

Go's ``tui/composer`` package surface, re-exported so callers keep writing
``composer.Model`` and ``composer.Submit``.
"""

from __future__ import annotations

from super_agent.tui.composer.model import (
    Command as Command,
    Intent as Intent,
    IntentKind as IntentKind,
    Model as Model,
    New as New,
    NormalizeCommands as NormalizeCommands,
)

#: Go's package-level intent constants.
NoIntent = IntentKind.NoIntent
Submit = IntentKind.Submit
Queue = IntentKind.Queue
Steer = IntentKind.Steer
Clear = IntentKind.Clear

__all__ = [
    "Clear",
    "Command",
    "Intent",
    "IntentKind",
    "Model",
    "New",
    "NoIntent",
    "NormalizeCommands",
    "Queue",
    "Steer",
    "Submit",
]
