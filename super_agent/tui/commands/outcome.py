"""What a command reports back, and the async results that follow it.

A command reports its whole effect at once: the error line, the status line, and
any scrollback output. Failure is carried as an exception value so the root can
apply the same precedence rules without unwinding.
"""

from __future__ import annotations

import dataclasses

__all__ = ["CompactDone", "MCPDone", "Outcome", "StatusBar"]


@dataclasses.dataclass(frozen=True, slots=True)
class StatusBar:
    """The session values a command changed on the root's status line.

    Only a command that switches agents moves the model, so an empty
    :attr:`model_name` keeps the value the status line already shows.
    """

    model_name: str = ""
    permission_mode: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class Outcome:
    """A command's request to the root.

    Every field is a separate, explicit request: the root applies the ones it
    owns and routes the cross-feature ones to the feature that owns them. A
    ``None`` outcome means the message produced no visible effect.
    """

    status: str = ""
    err: str = ""
    #: Committed to terminal scrollback rather than the live view.
    output: str = ""
    #: Starts a new turn with this text, which is how workflow commands and
    #: expanded custom commands submit their instructions.
    prompt: str = ""
    #: Queues a workspace file through the attachments feature.
    attach_path: str = ""
    #: Re-read conversation state after a command replaced, restored, or shrank
    #: the transcript.
    refresh_snapshot: bool = False
    status_bar: StatusBar | None = None
    show_help: bool = False
    quit: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class CompactDone:
    """The result of an asynchronous ``/compact`` run."""

    err: BaseException | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class MCPDone:
    """The result of an asynchronous MCP lifecycle change."""

    status: str = ""
    err: BaseException | None = None
