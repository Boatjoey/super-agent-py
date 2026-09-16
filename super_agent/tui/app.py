"""The composition root: the model, its construction inputs, and its options.

``App`` owns application lifecycle, global message routing, focus, terminal
dimensions, and layout composition. Every user capability lives in the feature
that owns it, so nothing here knows how a command, an approval, or an attachment
behaves.

The package surface — ``New``, ``infoBar``, ``welcomeString``, ``footerView``,
and ``applyOutcome`` — lives in ``super_agent/tui/__init__.py``, because those
functions read the whole model and wire several features together.
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
    "WithClipboardWriter",
    "WithOutputPrinter",
    "compactStatus",
    "composerCommands",
    "displayCWD",
    "printCommand",
    "scrollbackPrinter",
]


@dataclasses.dataclass(frozen=True, slots=True)
class StartupInfo:
    """What the process knew at start-up and the TUI only displays."""

    ModelName: str = ""
    PermissionMode: str = ""
    NoTools: bool = False
    CWD: str = ""
    InstructionPaths: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class SubmitDone:
    """A turn finished. Carries the failure, if it failed."""

    Err: BaseException | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class ConversationNotificationMsg:
    """One notification, tagged with the turn that will consume it.

    The tag is what makes a stale listener harmless: a notification from a turn
    that has already been replaced is dropped instead of applied.
    """

    Notification: ConversationNotification
    Turn: int = 0


def scrollbackPrinter(content: str) -> runtime.Command[Msg] | None:
    """The default command output: committed above the live view."""
    if not content.strip():
        return None
    return runtime.Scrollback(Content=content.rstrip("\n"))


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
    turn: int = 0
    writeClipboard: ClipboardWriter = dataclasses.field(default_factory=lambda: actions.defaultClipboardWrite)
    printOutput: OutputPrinter = dataclasses.field(default_factory=lambda: scrollbackPrinter)

    def Init(self) -> tuple[runtime.Command[Msg], ...]:
        """The commands a freshly built app starts with.

        Only the attachment load is left: the composer has no asynchronous
        effect of its own.
        """
        return (self.attachments.Init(),)

    def needsInput(self) -> bool:
        """Whether the runtime is waiting on the user rather than on work."""
        return self.agentStatus.AwaitingApproval


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
#: A printer that commits command output to scrollback.
type OutputPrinter = Callable[[str], runtime.Command[Msg] | None]
#: A construction-time adjustment to the app.
type Option = Callable[[App], None]


def WithClipboardWriter(write: ClipboardWriter) -> Option:
    """Replace the clipboard writer, for a platform or a test."""

    def apply(app: App) -> None:
        app.writeClipboard = write

    return apply


def WithOutputPrinter(printer: OutputPrinter) -> Option:
    """Replace the command-output printer, for scrollback or a test."""

    def apply(app: App) -> None:
        app.printOutput = printer

    return apply


def composerCommands(palette: Sequence[commands.Command]) -> tuple[ComposerCommand, ...]:
    """Map the command feature's palette onto the composer's own entry type.

    The composer never learns the command catalogue. This is the one place the
    two features meet, and the root owns it.
    """
    return tuple(ComposerCommand(Name=entry.Name, Description=entry.Description) for entry in palette)


def printCommand(app: App, content: str) -> runtime.Command[Msg] | None:
    """Commit command output to scrollback, or do nothing when there is none."""
    if not content.strip():
        return None
    printer = app.printOutput
    return printer(content.rstrip("\n"))


def displayCWD(cwd: str) -> str:
    """``cwd`` with the home directory abbreviated to ``~``."""
    home = os.path.expanduser("~")
    if home and home != "~" and cwd.startswith(home):
        return "~" + cwd[len(home) :]
    return cwd


def compactStatus(value: str, maxLines: int) -> str:
    """The first ``maxLines`` lines of a multi-line status, and what is left over."""
    lines = value.split("\n")
    if len(lines) <= maxLines:
        return value
    return "\n".join(lines[:maxLines]) + f"\n… {len(lines) - maxLines} more lines"
