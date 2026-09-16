"""A switchable workspace binding and the session filesystem adapter.

Ported from ``workspace/workspace.go``. The binding is protected by a lock
because it is mutable: a resume swaps the whole :class:`Context` atomically, and
the built-in tools hold the same binding, so a half-swapped context would let one
turn resolve paths against the old roots and the next against the new ones.

The adapter implements the session's :class:`~super_agent.runtime.session.Workspace`
port — ``Spec``, ``Validate``, ``Canonicalize``, ``Activate``, ``Capture``,
``Restore`` — and the narrower attachment and export ports on top of it.
"""

from __future__ import annotations

import base64
import contextlib
import os
import tempfile
import threading

from super_agent.runtime.session import (
    Attachment,
    FileSnapshot,
    WorkspaceAccessMode,
    WorkspaceAccessRead,
    WorkspaceAccessReadWrite,
    WorkspaceRootSpec,
    WorkspaceSpec,
)
from super_agent.workspace.context import (
    Access,
    AccessRead,
    AccessReadWrite,
    Context,
    NewContext,
    Root,
)


class Workspace:
    """The current :class:`Context`, plus the session's filesystem use cases."""

    def __init__(self, context: Context | None = None) -> None:
        self._context = context
        self._lock = threading.Lock()

    # --- the switchable binding ---------------------------------------------

    def GetPrimaryRoot(self) -> str:
        context = self._currentOrNone()
        return "" if context is None else context.GetPrimaryRoot()

    def GetCWD(self) -> str:
        context = self._currentOrNone()
        return "" if context is None else context.GetCWD()

    def ResolvePath(self, path: str) -> str:
        return self._current().ResolvePath(path)

    def CanRead(self, path: str) -> bool:
        context = self._currentOrNone()
        return context is not None and context.CanRead(path)

    def CanWrite(self, path: str) -> bool:
        context = self._currentOrNone()
        return context is not None and context.CanWrite(path)

    def Spec(self) -> WorkspaceSpec:
        """The durable description of the active context."""
        context = self._currentOrNone()
        if context is None:
            return WorkspaceSpec()
        return specFromContext(context)

    def Validate(self, spec: WorkspaceSpec) -> None:
        """Raise unless ``spec`` resolves to the same paths on the current filesystem."""
        contextFromSpec(spec)

    def Canonicalize(self, spec: WorkspaceSpec) -> WorkspaceSpec:
        """Re-resolve every path in ``spec`` against the current filesystem.

        Session persistence uses it exactly once to upgrade legacy metadata,
        whose saved cwd never promised a canonical path. New specs are validated
        strictly and never pass through here.
        """
        return specFromContext(buildContext(spec))

    def Activate(self, spec: WorkspaceSpec) -> None:
        """Atomically switch to a context rebuilt and validated from ``spec``."""
        context = contextFromSpec(spec)
        with self._lock:
            self._context = context

    def _current(self) -> Context:
        context = self._currentOrNone()
        if context is None:
            raise ValueError("workspace context is not configured")
        return context

    def _currentOrNone(self) -> Context | None:
        with self._lock:
            return self._context

    # --- attachments, exports, checkpoints -----------------------------------

    def ReadAttachment(self, path: str) -> Attachment:
        """Read one file as a base64 attachment, capped at 10 MiB."""
        absolute = self._readable(path)
        capture(absolute)
        info = os.stat(absolute)
        if info.st_size > 10 << 20:
            raise ValueError("attachment exceeds 10 MiB")
        with open(absolute, "rb") as handle:
            content = handle.read()
        return Attachment(
            Name=os.path.basename(absolute),
            MIME=detectContentType(content),
            Data=base64.standard_b64encode(content).decode("ascii"),
        )

    def WriteExport(self, relative: str, content: bytes) -> str:
        """Write an export atomically under the writable roots and return its path."""
        path = self._writable(relative)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".export-")
        try:
            os.chmod(temporary, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
            os.replace(temporary, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.remove(temporary)
            raise
        return path

    def Capture(self, paths: list[str]) -> list[FileSnapshot]:
        """Snapshot each path before a mutating tool call touches it."""
        return [capture(self._writable(path)) for path in paths]

    def Restore(self, files: list[FileSnapshot]) -> None:
        """Put snapshotted files back, deleting the ones that did not exist."""
        for file in files:
            path = self._writable(file.Path)
            if not file.Exists:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(path)
                continue
            os.makedirs(os.path.dirname(path), mode=0o755, exist_ok=True)
            mode = file.Mode if file.Mode != 0 else 0o644
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            try:
                os.write(descriptor, file.Content.encode("utf-8"))
            finally:
                os.close(descriptor)

    # --- path resolution helpers ---------------------------------------------

    def _readable(self, path: str) -> str:
        resolved = self.ResolvePath(path)
        if not self.CanRead(resolved):
            raise ValueError("path is outside readable workspace roots")
        return resolved

    def _writable(self, path: str) -> str:
        resolved = self.ResolvePath(path)
        if not self.CanWrite(resolved):
            raise ValueError("path is outside writable workspace roots")
        return resolved


def New(context: Context) -> Workspace:
    """A binding over ``context``, mirroring Go's ``workspace.New``."""
    return Workspace(context)


def contextFromSpec(spec: WorkspaceSpec) -> Context:
    """Rebuild a context from ``spec`` and require every saved path to be unchanged.

    A root that now resolves somewhere else — moved, deleted, or replaced by an
    escaping symlink — is a hard failure, so resume never silently adopts a
    different directory than the one that was saved.
    """
    context = buildContext(spec)
    if not samePath(spec.PrimaryRoot, context.GetPrimaryRoot()) or not samePath(spec.CWD, context.GetCWD()):
        raise ValueError("saved workspace primary root or cwd resolves to a different path")
    resolvedRoots = context.GetRoots()
    for index, root in enumerate(spec.Roots):
        if not samePath(root.Path, resolvedRoots[index].Path):
            raise ValueError("saved workspace root resolves to a different path")
    return context


def buildContext(spec: WorkspaceSpec) -> Context:
    """Turn the durable description into a context, before the identity check."""
    if not os.path.isabs(spec.PrimaryRoot) or not os.path.isabs(spec.CWD) or not spec.Roots:
        raise ValueError("workspace spec requires absolute primary root, cwd, and roots")
    roots: list[Root] = []
    for root in spec.Roots:
        if not os.path.isabs(root.Path):
            raise ValueError("workspace spec root is not absolute")
        roots.append(Root(Path=root.Path, Access=fromSessionAccess(root.Access)))
    return NewContext(spec.PrimaryRoot, spec.CWD, roots)


def specFromContext(context: Context) -> WorkspaceSpec:
    """The durable description of a context."""
    roots = tuple(WorkspaceRootSpec(Path=root.Path, Access=toSessionAccess(root.Access)) for root in context.GetRoots())
    return WorkspaceSpec(PrimaryRoot=context.GetPrimaryRoot(), CWD=context.GetCWD(), Roots=roots)


def samePath(saved: str, resolved: str) -> bool:
    """Whether two spellings name the same path."""
    try:
        relative = os.path.relpath(os.path.normpath(resolved), os.path.normpath(saved))
    except ValueError:
        return False
    return relative == "."


def toSessionAccess(access: Access) -> WorkspaceAccessMode:
    if access == AccessReadWrite:
        return WorkspaceAccessReadWrite
    return WorkspaceAccessRead


def fromSessionAccess(access: WorkspaceAccessMode) -> Access:
    if access == WorkspaceAccessRead:
        return AccessRead
    if access == WorkspaceAccessReadWrite:
        return AccessReadWrite
    raise ValueError("invalid saved workspace access mode")


def capture(path: str) -> FileSnapshot:
    """Snapshot one file: existence, content, and permission bits.

    Non-UTF-8 bytes become U+FFFD, which is exactly what Go's JSON encoder writes
    for a snapshot it has to persist; ordinary text round-trips unchanged.
    """
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return FileSnapshot(Path=path, Exists=False)
    with open(path, "rb") as handle:
        content = handle.read()
    return FileSnapshot(
        Path=path,
        Exists=True,
        Content=content.decode("utf-8", errors="replace"),
        Mode=info.st_mode & 0o777,
    )


def detectContentType(content: bytes) -> str:
    """A small stand-in for Go's ``http.DetectContentType``.

    The MIME string never reaches disk, so only the shapes the session cares
    about are reproduced: the common image and document signatures, a text sniff
    matching Go's control-byte rule, and ``application/octet-stream`` otherwise.
    """
    head = content[:512]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"PK\x03\x04"):
        return "application/zip"
    if head.startswith(b"\x1f\x8b\x08"):
        return "application/x-gzip"
    if head.startswith(b"BM"):
        return "image/bmp"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if _isBinary(head):
        return "application/octet-stream"
    return "text/plain; charset=utf-8"


def _isBinary(content: bytes) -> bool:
    """Go's binary sniff: a control byte other than tab, newline, form feed, CR, or escape."""
    allowed = {0x09, 0x0A, 0x0C, 0x0D, 0x1B}
    return any(byte < 0x20 and byte not in allowed for byte in content)
