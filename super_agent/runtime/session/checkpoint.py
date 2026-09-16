"""Capturing a file's contents before a mutating tool call, so undo is possible."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from super_agent.runtime.machine import ToolCall
from super_agent.runtime.session.repository import FileSnapshot

if TYPE_CHECKING:
    from super_agent.runtime.session.repository import Repository, SessionID, Workspace


class CheckpointMixin:
    """The checkpoint use case."""

    if TYPE_CHECKING:
        repository: Repository | None
        workspace: Workspace | None

        def metaID(self) -> SessionID: ...

    def checkpoint(self, call: ToolCall) -> None:
        """Record the files ``call`` is about to change.

        A session without persistence or without a workspace simply has nowhere to
        keep a checkpoint, which is not an error: it just cannot undo.
        """
        if self.repository is None or self.workspace is None:
            return
        paths = checkpointPaths(call)
        if not paths:
            return
        files: list[FileSnapshot] = self.workspace.capture(paths)
        self.repository.save_checkpoint(self.metaID(), call, files)


def checkpointPaths(call: ToolCall) -> list[str]:
    """Which files a tool call will touch, from its own argument shape.

    ``write_file`` and ``apply_patch`` name one ``path``; ``format`` names a
    ``files`` list. Anything else leaves nothing to restore.
    """
    if call.name in ("write_file", "apply_patch"):
        args = _parse(call.input)
        path = args.get("path") if args is not None else None
        if isinstance(path, str) and path != "":
            return [path]
        return []
    if call.name == "format":
        args = _parse(call.input)
        files = args.get("files") if args is not None else None
        if isinstance(files, list):
            return [item for item in cast("list[Any]", files) if isinstance(item, str)]
    return []


def _parse(input: str) -> dict[str, Any] | None:
    try:
        parsed: Any = json.loads(input)
    except (TypeError, ValueError):
        return None
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None
