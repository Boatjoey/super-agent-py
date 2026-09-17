"""The composition root: the model, its construction inputs, and its options.

``App`` owns application lifecycle, global message routing, focus, terminal
dimensions, and layout composition. Every user capability lives in the feature
that owns it, so nothing here knows how a command, an approval, or an attachment
behaves.

The package surface — ``new``, ``welcomeString``, and ``applyOutcome`` — lives
in ``super_agent/tui/__init__.py``, because those functions read the whole model
and wire several features together.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable, Sequence

from super_agent.tui import actions, attachments, commands, runtime
from super_agent.tui.approval import Model as ApprovalModel
from super_agent.tui.attachments import Model as AttachmentsModel
from super_agent.tui.composer import Command as ComposerCommand, Model as ComposerModel
from super_agent.tui.conversation import (
    AgentStatus,
    ApprovalDecision,
    Cancellation,
    Channel,
    ContextUsage,
    ConversationNotification,
    SnapshotPort,
    TurnPort,
)
from super_agent.tui.styles import Styles
from super_agent.tui.transcript import Model as TranscriptModel

__all__ = [
    "App",
    "ClipboardWriter",
    "ConversationNotificationMsg",
    "Msg",
    "Option",
    "OutputPrinter",
    "StartupInfo",
    "SubmitDone",
    "composerCommands",
    "displayCWD",
    "printCommand",
    "with_clipboard_writer",
    "with_output_printer",
]


@dataclasses.dataclass(frozen=True, slots=True)
class StartupInfo:
    """What the process knew at start-up and the TUI only displays.

    ``status_line`` carries the ``tui.status_line`` setting, and its three values
    are the three documented meanings: ``None`` draws the default order, ``()``
    removes the row, and a tuple draws exactly the items it names. The setting was
    validated when the configuration was loaded, so every name here is one the
    status line knows.
    """

    model_name: str = ""
    permission_mode: str = ""
    no_tools: bool = False
    cwd: str = ""
    instruction_paths: tuple[str, ...] = ()
    status_line: tuple[str, ...] | None = None
    session_id: str = ""
    sandbox: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class SubmitDone:
    """A turn finished. Carries the failure, if it failed."""

    err: BaseException | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ConversationNotificationMsg:
    """One notification, tagged with the turn that will consume it.

    The tag is what makes a stale listener harmless: a notification from a turn
    that has already been replaced is dropped instead of applied.
    """

    notification: ConversationNotification
    turn: int = 0


@dataclasses.dataclass(slots=True)
class App:
    """The composition root and the global router."""

    snapshot: SnapshotPort
    turnPort: TurnPort
    commands: commands.Model
    composer: ComposerModel
    approval: ApprovalModel
    attachments: AttachmentsModel
    transcript: TranscriptModel
    styles: Styles
    info: StartupInfo
    ready: bool = False
    width: int = 80
    height: int = 24
    showHelp: bool = False
    err: str = ""
    status: str = ""
    cancellation: Cancellation | None = None
    notifications: Channel[ConversationNotification] = dataclasses.field(
        default_factory=lambda: Channel[ConversationNotification]()
    )
    approvals: Channel[ApprovalDecision] = dataclasses.field(default_factory=lambda: Channel[ApprovalDecision]())
    agentStatus: AgentStatus = dataclasses.field(default_factory=AgentStatus)
    contextUsage: ContextUsage | None = None
    turn: int = 0
    writeClipboard: ClipboardWriter = dataclasses.field(default_factory=lambda: actions.defaultClipboardWrite)
    #: Where command output goes. The application shell installs its own viewer
    #: at construction, so a bare model has nowhere to print and drops it.
    printOutput: OutputPrinter | None = None

    def init(self) -> tuple[runtime.Command[Msg], ...]:
        """The commands a freshly built app starts with.

        Only the attachment load is left: the composer has no asynchronous
        effect of its own.
        """
        return (self.attachments.init(),)

    def needsInput(self) -> bool:
        """Whether the runtime is waiting on the user rather than on work."""
        return self.agentStatus.awaiting_approval


#: Every message the app's update accepts: the terminal's, the turn's, and each
#: feature's result. Closed on purpose — nothing else may reach the loop.
type Msg = (
    runtime.WindowSizeMsg
    | runtime.KeyMsg
    | runtime.QuitMsg
    | runtime.ClearScreenMsg
    | SubmitDone
    | ConversationNotificationMsg
    | actions.ClipboardDone
    | attachments.Loaded
    | attachments.Attached
    | commands.CompactDone
    | commands.MCPDone
)

#: An asynchronous clipboard write: native tools and OSC 52 are both children.
type ClipboardWriter = Callable[[str], None]
#: A printer that shows command output in the shell's own viewer.
type OutputPrinter = Callable[[str], runtime.Command[Msg] | None]
#: A construction-time adjustment to the app.
type Option = Callable[[App], None]


def with_clipboard_writer(write: ClipboardWriter) -> Option:
    """Replace the clipboard writer, for a platform or a test."""

    def apply(app: App) -> None:
        app.writeClipboard = write

    return apply


def with_output_printer(printer: OutputPrinter) -> Option:
    """Replace the command-output printer, for scrollback or a test."""

    def apply(app: App) -> None:
        app.printOutput = printer

    return apply


def composerCommands(palette: Sequence[commands.Command]) -> tuple[ComposerCommand, ...]:
    """Map the command feature's palette onto the composer's own entry type.

    The composer never learns the command catalogue. This is the one place the
    two features meet, and the root owns it.
    """
    return tuple(ComposerCommand(name=entry.name, description=entry.description) for entry in palette)


def printCommand(app: App, content: str) -> runtime.Command[Msg] | None:
    """Commit command output to the shell's viewer, or do nothing when there is none."""
    printer = app.printOutput
    if printer is None or not content.strip():
        return None
    return printer(content.rstrip("\n"))


def displayCWD(cwd: str) -> str:
    """``cwd`` with the home directory abbreviated to ``~``."""
    home = os.path.expanduser("~")
    if home and home != "~" and cwd.startswith(home):
        return "~" + cwd[len(home) :]
    return cwd
