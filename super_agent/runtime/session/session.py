"""The session: use cases over the engine, with persistence and filesystem ports.

The session never schedules actions. Approval travels the engine's own
scheduled-action path, so this layer only supplies ports: an approval waiter, a
streaming callback, a per-turn state observer, and the repository calls that make
each of them durable.

Python cannot split one class across modules, so the session is assembled from
mixins that mirror the Go file layout: ``turn.py``, ``history.py``,
``persistence.py``, ``checkpoint.py``, ``attachments.py``, ``export.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from super_agent.errors import JoinedError
from super_agent.runtime.engine import Engine, EngineView
from super_agent.runtime.execution import (
    PermissionMode,
    PermissionModeAsk,
    PermissionModeBypass,
    PermissionRules,
    ValidPermissionMode,
)
from super_agent.runtime.machine import Message
from super_agent.runtime.protocol.types import Attachment
from super_agent.runtime.session.attachments import AttachmentsMixin
from super_agent.runtime.session.checkpoint import CheckpointMixin
from super_agent.runtime.session.export import ExportMixin
from super_agent.runtime.session.history import HistoryMixin
from super_agent.runtime.session.notifications import SessionNotification
from super_agent.runtime.session.persistence import PersistenceMixin
from super_agent.runtime.session.repository import (
    Metadata,
    Repository,
    SessionID,
    Workspace,
    WorkspaceSpec,
    workspaceSpecPointer,
)
from super_agent.runtime.session.snapshot_emitter import SnapshotEmitter
from super_agent.runtime.session.turn import TurnMixin


class Closer(Protocol):
    """Something released when the session closes, such as the telemetry sink."""

    def Close(self) -> None: ...


class SessionBase:
    """State and lifecycle, mirroring Go's ``session.go``.

    ``_turnActive`` stands in for Go's ``sync.Mutex`` with ``TryLock``: every
    caller runs on the same event loop and the check-and-set never yields, so a
    flag gives the same "fail fast when a turn is running" behaviour.
    """

    def __init__(
        self,
        engine: Engine,
        repository: Repository | None = None,
        workspace: Workspace | None = None,
        meta: Metadata | None = None,
        emitter: SnapshotEmitter | None = None,
        initial: list[Message] | None = None,
    ) -> None:
        self.engine = engine
        self.emitter = emitter if emitter is not None else SnapshotEmitter()
        self.repository = repository
        self.workspace = workspace
        self.meta = meta if meta is not None else Metadata()
        self.permissionMode = PermissionMode("")
        self.permissionRules = PermissionRules()
        self._turn_active = False
        self._closers: list[Closer] = []
        self._attachments: list[Attachment] = []
        if initial:
            self.emitter.emittedMessages = len(initial)

    # --- the turn lock -------------------------------------------------------

    def _tryLock(self) -> bool:
        """Go's ``TryLock``: succeed or report that a turn is already running."""
        if self._turn_active:
            return False
        self._turn_active = True
        return True

    def _unlock(self) -> None:
        self._turn_active = False

    # --- lifecycle -----------------------------------------------------------

    def AddCloser(self, closer: Closer | None) -> None:
        """Register something to release when the session closes."""
        if closer is None:
            return
        self._closers.append(closer)

    async def Close(self) -> None:
        """Cancel the turn, then release every closer in reverse order.

        Errors are aggregated rather than dropped: a sink that failed to flush is
        worth reporting even when three others closed cleanly.
        """
        await self.Cancel()
        closers = self._closers
        self._closers = []
        failures: list[BaseException] = []
        for closer in reversed(closers):
            try:
                closer.Close()
            except Exception as error:
                failures.append(error)
        if failures:
            raise JoinedError(*failures)

    async def Cancel(self) -> None:
        """Cancel the run, and record that the user asked for it."""
        await self.engine.Cancel()
        if self.repository is not None:
            self.repository.SaveCancel(self.metaID())

    async def Reset(self) -> None:
        """Clear the conversation, persisting the reset before mutating.

        Reset stays available while a turn runs: the engine drops stale results
        and the emitter is internally synchronised, so resetting mid-run clears
        the conversation without racing the turn.
        """
        if self.repository is not None:
            self.repository.SaveReset(self.metaID())
        await self.engine.Reset()
        self.emitter.reset(len(self.engine.Snapshot().Messages))

    async def ReplaceConversation(self, messages: list[Message]) -> None:
        """Activate a new system context and clear prior turns."""
        if not self._tryLock():
            raise RuntimeError("session is already running a turn")
        try:
            if self.repository is not None:
                self.repository.SaveConversationReplacement(self.metaID(), messages)
            await self.engine.ReplaceMessages(messages)
            self.emitter.reset(len(messages))
        finally:
            self._unlock()

    # --- metadata and permissions -------------------------------------------

    def metaID(self) -> SessionID:
        """The active session id.

        ``Cancel`` runs lock-free, so this reads through the same field it would
        otherwise race with during a resume.
        """
        return self.meta.ID

    def Metadata(self) -> Metadata:
        return self.meta

    def Snapshot(self) -> EngineView:
        return self.engine.Snapshot()

    def ConfigurePermissions(self, mode: PermissionMode, rules: PermissionRules) -> None:
        self.permissionMode = mode
        self.permissionRules = rules

    def PermissionMode(self) -> PermissionMode:
        """The active policy mode, for display and queries."""
        return self.permissionMode

    def AutoApproveTools(self) -> bool:
        """Whether the active policy approves tools without prompting.

        A runtime decision, not something the TUI derives from the mode string.
        """
        return self.permissionMode == PermissionModeBypass

    async def SetPermissionMode(self, mode: PermissionMode) -> None:
        if mode == "":
            mode = PermissionModeAsk
        if not ValidPermissionMode(mode):
            raise ValueError("invalid permission mode: " + str(mode))
        await self.engine.SetPermissionPolicy(mode, self.permissionRules)
        self.permissionMode = mode

    async def emitSnapshot(self, notifications: asyncio.Queue[SessionNotification]) -> None:
        """Push the current snapshot through the emitter."""
        await self.emitter.emit(notifications, self.Snapshot(), self.persistMessage)

    def persistMessage(self, message: Message) -> None:
        """Persist one appended message. Overridden by :class:`PersistenceMixin`."""
        if self.repository is not None:
            self.repository.SaveMessage(self.metaID(), message)


def NewSession(engine: Engine) -> Session:
    """A session with no persistence: the conversation lives only in memory."""
    return Session(engine)


def NewPersistentSession(
    engine: Engine,
    repository: Repository | None,
    workspace: Workspace | None,
    meta: Metadata,
) -> Session:
    """A session over an existing stored conversation."""
    emitter = SnapshotEmitter()
    emitter.emittedMessages = len(engine.Snapshot().Messages)
    return Session(engine, repository, workspace, meta, emitter)


def CreatePersistentSession(
    engine: Engine,
    repository: Repository,
    workspace: Workspace | None,
    meta: Metadata,
    initial: list[Message],
) -> Session:
    """Create the stored session first, so a failure leaves no half-made session.

    The spec is validated *before* anything is written: a session whose saved
    workspace could never be restored is worse than no session at all.
    """
    if workspace is None:
        raise ValueError("workspace is not configured")
    spec = workspace.Spec()
    workspace.Validate(spec)
    created = repository.Create(
        _with_workspace(meta, spec),
        initial,
    )
    return NewPersistentSession(engine, repository, workspace, created)


def _with_workspace(meta: Metadata, spec: WorkspaceSpec) -> Metadata:
    return Metadata(
        ID=meta.ID,
        Title=meta.Title,
        Provider=meta.Provider,
        Model=meta.Model,
        CWD=spec.CWD,
        InstructionSources=meta.InstructionSources,
        ParentID=meta.ParentID,
        ProjectID=meta.ProjectID,
        ConfigRoot=meta.ConfigRoot,
        WorkspaceSpec=workspaceSpecPointer(spec),
    )


class Session(
    TurnMixin,
    HistoryMixin,
    PersistenceMixin,
    CheckpointMixin,
    AttachmentsMixin,
    ExportMixin,
    SessionBase,
):
    """Use cases over one engine, with persistence and filesystem ports."""


# ``Callable`` and ``Attachment`` are referenced by the mixin annotations that
# callers rely on; importing them here keeps that surface in one place.
_ = (Callable, Attachment)
