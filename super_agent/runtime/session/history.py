"""History use cases: listing, forking, memory, resume, compaction, and undo.

Every one of these rewrites the conversation, so every one of them takes the turn
lock and refuses to run while a turn is in flight. Where a write could fail, it
happens before the in-memory change so a failure leaves one consistent side.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from super_agent.runtime.machine import Message, RoleSystem, RoleTool
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.session.repository import (
    Metadata,
    Repository,
    SessionID,
    Summary,
    WorkspaceAccessReadWrite,
    WorkspaceRootSpec,
    WorkspaceSpec,
    workspaceSpecPointer,
)

if TYPE_CHECKING:
    from super_agent.runtime.engine import Engine, EngineView
    from super_agent.runtime.session.repository import Workspace
    from super_agent.runtime.session.snapshot_emitter import SnapshotEmitter

#: The prefix that marks the memory system message, so it can be replaced rather
#: than accumulated on every remember.
MEMORY_SYSTEM_PREFIX = "Cross-session memory:\n"


class HistoryMixin:
    """Conversation history use cases."""

    if TYPE_CHECKING:
        engine: Engine
        emitter: SnapshotEmitter
        repository: Repository | None
        workspace: Workspace | None
        meta: Metadata

        def _tryLock(self) -> bool: ...
        def _unlock(self) -> None: ...
        def metaID(self) -> SessionID: ...
        def Metadata(self) -> Metadata: ...
        def Snapshot(self) -> EngineView: ...

    # --- listing and forking -------------------------------------------------

    def ListSessions(self) -> list[Summary]:
        if self.repository is None:
            raise ValueError("session store is not configured")
        return self.repository.List()

    async def Fork(self, title: str) -> Metadata:
        """Copy this session's conversation into a new stored session."""
        if not self._tryLock():
            raise RuntimeError("session is already running a turn")
        try:
            if self.repository is None:
                raise ValueError("session store is not configured")
            current = self.Metadata()
            title = title.strip()
            if title == "":
                title = current.Title + " (fork)"
            messages = list(self.Snapshot().Messages)
            created = self.repository.Create(
                Metadata(
                    Title=title,
                    Provider=current.Provider,
                    Model=current.Model,
                    CWD=current.CWD,
                    InstructionSources=current.InstructionSources,
                    ParentID=current.ID,
                    ProjectID=current.ProjectID,
                    ConfigRoot=current.ConfigRoot,
                    WorkspaceSpec=_spec_from_workspace(self.workspace),
                ),
                messages,
            )
            await self.engine.ReplaceMessages(messages)
            self.meta = created
            self._rearmEmitter(len(messages))
            return created
        finally:
            self._unlock()

    # --- memory --------------------------------------------------------------

    def Memories(self) -> list[str]:
        if self.repository is None:
            raise ValueError("session store is not configured")
        return self.repository.LoadMemory()

    async def Remember(self, value: str) -> None:
        """Add one line to the cross-session memory, if it is not already there."""
        value = value.strip()
        if value == "":
            raise ValueError("memory text is required")
        if not self._tryLock():
            raise RuntimeError("session is already running a turn")
        try:
            if self.repository is None:
                raise ValueError("session store is not configured")
            items = self.repository.LoadMemory()
            if value in items:
                return
            items.append(value)
            self.repository.SaveMemory(items)
            await self._replaceMemoryContext(items)
        finally:
            self._unlock()

    async def ForgetMemories(self) -> None:
        if not self._tryLock():
            raise RuntimeError("session is already running a turn")
        try:
            if self.repository is None:
                raise ValueError("session store is not configured")
            self.repository.SaveMemory([])
            await self._replaceMemoryContext([])
        finally:
            self._unlock()

    async def _replaceMemoryContext(self, items: list[str]) -> None:
        assert self.repository is not None
        kept = withMemoryContext(list(self.Snapshot().Messages), items)
        self.repository.SaveConversationReplacement(self.metaID(), kept)
        await self.engine.ReplaceMessages(kept)
        self.emitter.reset(len(kept))

    # --- resume --------------------------------------------------------------

    async def Resume(self, session_id: SessionID) -> None:
        """Restore a stored session, upgrading legacy metadata exactly once.

        Every validation happens before anything is activated, so a resume that
        cannot succeed leaves the current session untouched. The resume never
        falls back to the process working directory: a saved workspace that no
        longer validates is a hard failure.
        """
        if not self._tryLock():
            raise RuntimeError("session is already running a turn")
        try:
            if self.repository is None:
                raise ValueError("session store is not configured")
            messages, meta = self.repository.Load(session_id)
            if self.workspace is None:
                raise ValueError("workspace is not configured")
            spec, legacy = savedWorkspaceSpec(meta)
            if legacy:
                # Sessions written before WorkspaceSpec saved only a cwd and
                # never promised it was canonical, so upgrade it once:
                # re-resolve the saved cwd against the current filesystem and
                # persist the result. Every later resume takes the strict path.
                try:
                    spec = self.workspace.Canonicalize(spec)
                except Exception as error:
                    raise ValueError(f"saved workspace is no longer valid: {error}") from error
            try:
                self.workspace.Validate(spec)
            except Exception as error:
                raise ValueError(f"saved workspace is no longer valid: {error}") from error
            if legacy:
                # Persist before mutating the active session so a failed write
                # leaves the resume untouched instead of half applied.
                try:
                    self.repository.SaveWorkspaceDescription(session_id, spec)
                except Exception as error:
                    raise ValueError(f"persist migrated workspace: {error}") from error
            memories = self.repository.LoadMemory()
            messages = withMemoryContext(messages, memories)
            self.repository.SaveConversationReplacement(session_id, messages)
            try:
                self.workspace.Activate(spec)
            except Exception as error:
                raise ValueError(f"saved workspace is no longer valid: {error}") from error

            meta = _with_workspace(meta, spec)
            await self.engine.ReplaceMessages(messages)
            self.meta = meta
            self._rearmEmitter(len(messages))
        finally:
            self._unlock()

    def RenameSession(self, session_id: SessionID, title: str) -> None:
        if self.repository is None:
            raise ValueError("session store is not configured")
        self.repository.RenameSession(session_id, title)

    def DeleteSession(self, session_id: SessionID) -> None:
        if self.repository is None:
            raise ValueError("session store is not configured")
        if session_id == self.metaID():
            raise ValueError("cannot delete active session")
        self.repository.Delete(session_id)

    # --- compaction and undo -------------------------------------------------

    async def Compact(self, ctx: RunContext, summary: str, keepNewest: int) -> None:
        """Replace older turns with a summary, keeping the newest few.

        The compaction record is persisted before the engine is replaced, so the
        in-memory and on-disk transcripts cannot disagree.
        """
        if not self._tryLock():
            raise RuntimeError("session is already running a turn")
        try:
            if self.repository is None:
                raise ValueError("session store is not configured")
            snapshot = self.Snapshot()
            if not snapshot.Messages:
                return
            if keepNewest < 1:
                keepNewest = 4
            current = list(snapshot.Messages)
            if nonSystemMessageCount(current) <= keepNewest:
                return
            summary = summary.strip()
            if summary == "":
                summary = await self.engine.CompactSummary(ctx)
            kept = compactedMessages(current, summary, keepNewest)
            original = list(current)
            self.repository.SaveCompaction(self.metaID(), summary, original, kept)
            await self.engine.ReplaceMessages(kept)
            # The kept messages were already emitted and persisted through the
            # compaction record; re-emitting them would duplicate the transcript.
            self.emitter.emittedMessages = len(kept)
        finally:
            self._unlock()

    async def Undo(self) -> None:
        """Restore the workspace to the last checkpoint and drop what came after.

        The files are restored first. Only when that succeeds is the transcript
        truncated, so a failed restore cannot lose history for good.
        """
        if not self._tryLock():
            raise RuntimeError("session is already running a turn")
        try:
            if self.repository is None:
                raise ValueError("session store is not configured")
            if self.workspace is None:
                raise ValueError("workspace is not configured")
            try:
                files, messages, index = self.repository.LoadUndoPoint(self.metaID())
            except Exception as error:
                raise ValueError("no checkpoint to undo") from error
            self.workspace.Restore(files)
            self.repository.TruncateAfter(self.metaID(), index)
            await self.engine.ReplaceMessages(messages)
            self.emitter.emittedMessages = len(messages)
        finally:
            self._unlock()

    def _rearmEmitter(self, emitted: int) -> None:
        from super_agent.runtime.session.snapshot_emitter import SnapshotEmitter

        self.emitter = SnapshotEmitter()
        self.emitter.emittedMessages = emitted


def nonSystemMessageCount(messages: list[Message]) -> int:
    return sum(1 for message in messages if message.Role != RoleSystem)


def compactedMessages(messages: list[Message], summary: str, keepNewest: int) -> list[Message]:
    """Keep every system message, add the summary, and keep the newest turns.

    When the kept window would start on a ``tool`` message, it is widened
    backwards: a tool result with no preceding tool call is a transcript the
    provider rejects.
    """
    system = [message for message in messages if message.Role == RoleSystem]
    rest = [message for message in messages if message.Role != RoleSystem]
    if len(rest) <= keepNewest:
        return [*system, *rest]
    kept = [*system, Message(Role=RoleSystem, Content="Conversation summary:\n" + summary)]
    start = len(rest) - keepNewest
    if rest[start].Role == RoleTool:
        while start > 0 and rest[start].Role == RoleTool:
            start -= 1
    return [*kept, *rest[start:]]


def withMemoryContext(messages: list[Message], items: list[str]) -> list[Message]:
    """Put the cross-session memory at the front, replacing any previous copy."""
    kept = [
        message
        for message in messages
        if message.Role != RoleSystem or not message.Content.startswith(MEMORY_SYSTEM_PREFIX)
    ]
    if items:
        memory = Message(Role=RoleSystem, Content=MEMORY_SYSTEM_PREFIX + "- " + "\n- ".join(items))
        return [memory, *kept]
    return kept


def savedWorkspaceSpec(meta: Metadata) -> tuple[WorkspaceSpec, bool]:
    """The durable workspace description a resume must restore.

    The second element marks metadata written before ``WorkspaceSpec``: it carries
    only a cwd, which the resume upgrades to a canonical spec exactly once.
    """
    if meta.WorkspaceSpec is not None:
        return workspaceSpecPointer(meta.WorkspaceSpec), False
    if meta.CWD == "":
        raise ValueError("saved workspace is no longer valid: legacy session has no cwd")
    return (
        WorkspaceSpec(
            PrimaryRoot=meta.CWD,
            CWD=meta.CWD,
            Roots=(WorkspaceRootSpec(Path=meta.CWD, Access=WorkspaceAccessReadWrite),),
        ),
        True,
    )


def _spec_from_workspace(workspace: Workspace | None) -> WorkspaceSpec | None:
    if workspace is None:
        return None
    return workspaceSpecPointer(workspace.Spec())


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
