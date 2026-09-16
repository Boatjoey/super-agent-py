"""Workspace file tools and the argument plumbing they share.

Reading and writing go through the injected workspace context, and the open itself
refuses a symlink final component so the containment check cannot be undone
between resolve and open.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import json
import os
import re
import stat
import sys
from typing import IO, Any, Final, cast

from super_agent.jsonutil import from_json_value, json_field
from super_agent.runtime.protocol.run_context import RunContext
from super_agent.runtime.protocol.types import ToolCall, ToolSpec
from super_agent.tools.workspace import WorkspaceContext, resolve_readable, resolve_writable

if sys.platform == "win32":
    from super_agent.tools.nofollow_other import (
        open_workspace_file as open_workspace_file,
        write_file_no_follow as write_file_no_follow,
    )
else:
    from super_agent.tools.nofollow_unix import (
        open_workspace_file as open_workspace_file,
        write_file_no_follow as write_file_no_follow,
    )

max_tool_output_lines: Final[int] = 200
#: Bounds how much a single tool call loads into memory. Tool output is
#: truncated to ``max_tool_output_lines`` anyway, so reading a multi-gigabyte
#: file would only burn memory before truncation.
max_read_file_bytes: Final[int] = 10 << 20
#: Bounds one line during search so a minified or machine-generated file cannot
#: abort the whole workspace scan.
max_search_line_bytes: Final[int] = 1 << 20


@dataclasses.dataclass(frozen=True, slots=True)
class _ReadFileArgs:
    path: str = dataclasses.field(default="", metadata=json_field(name="path"))
    start_line: int = dataclasses.field(default=0, metadata=json_field(name="start_line"))
    end_line: int = dataclasses.field(default=0, metadata=json_field(name="end_line"))


@dataclasses.dataclass(frozen=True, slots=True)
class _ListFilesArgs:
    path: str = dataclasses.field(default="", metadata=json_field(name="path"))
    pattern: str = dataclasses.field(default="", metadata=json_field(name="pattern"))


@dataclasses.dataclass(frozen=True, slots=True)
class _SearchArgs:
    query: str = dataclasses.field(default="", metadata=json_field(name="query"))
    path: str = dataclasses.field(default="", metadata=json_field(name="path"))


@dataclasses.dataclass(frozen=True, slots=True)
class _ApplyPatchArgs:
    path: str = dataclasses.field(default="", metadata=json_field(name="path"))
    old_text: str = dataclasses.field(default="", metadata=json_field(name="old_text"))
    new_text: str = dataclasses.field(default="", metadata=json_field(name="new_text"))
    replace_all: bool = dataclasses.field(default=False, metadata=json_field(name="replace_all"))


@dataclasses.dataclass(frozen=True, slots=True)
class _WriteFileArgs:
    path: str = dataclasses.field(default="", metadata=json_field(name="path"))
    content: str = dataclasses.field(default="", metadata=json_field(name="content"))


def decode_args[T](text: str, cls: type[T]) -> T:
    """Parse a tool call's raw JSON input into ``cls``.

    Every decoding failure surfaces to the model as the same message, so the
    distinction is not preserved.
    """
    try:
        return from_json_value(_json_object(text), cls)
    except (TypeError, ValueError) as err:
        raise ValueError("invalid JSON input") from err


def decode_json_object(text: str) -> dict[str, Any]:
    """Parse ``text`` as a JSON object, rejecting anything else."""
    try:
        return _json_object(text)
    except ValueError as err:
        raise ValueError("invalid JSON input") from err


def _json_object(text: str) -> dict[str, Any]:
    try:
        data: object = json.loads(text)
    except ValueError as err:
        raise ValueError("invalid JSON input") from err
    if not isinstance(data, dict):
        raise ValueError("invalid JSON input")
    return cast("dict[str, Any]", data)


def object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Build the JSON-schema object a :class:`ToolSpec` advertises."""
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


@dataclasses.dataclass(frozen=True, slots=True)
class ReadFileTool:
    """Read a workspace file, optionally restricted to a line range."""

    workspace: WorkspaceContext | None = None

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="read_file",
                description="Read a workspace file, optionally with start_line and end_line.",
                parameters=object_schema(
                    {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    ["path"],
                ),
            )
        ]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        args = decode_args(call.input, _ReadFileArgs)
        path, rel = resolve_readable(self.workspace, args.path)
        content = read_file_capped(path, max_read_file_bytes)
        if is_binary(content):
            raise RuntimeError(f"refusing to read binary file: {rel}")
        return numbered_lines(content.decode("utf-8", "replace"), args.start_line, args.end_line)


@dataclasses.dataclass(frozen=True, slots=True)
class ListFilesTool:
    """List workspace files under a path, optionally filtered by a glob."""

    workspace: WorkspaceContext | None = None

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="list_files",
                description="List workspace files under path, optionally filtered by glob pattern.",
                parameters=object_schema(
                    {"path": {"type": "string"}, "pattern": {"type": "string"}},
                    [],
                ),
            )
        ]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        args = _ListFilesArgs() if call.input == "" else decode_args(call.input, _ListFilesArgs)
        path = args.path or "."
        root, _ = resolve_readable(self.workspace, path)
        files = collect_files(self.workspace, root, args.pattern)
        return "\n".join(limit_lines(files))


@dataclasses.dataclass(frozen=True, slots=True)
class SearchTool:
    """Search workspace files by regular expression."""

    workspace: WorkspaceContext | None = None

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="search",
                description="Search text in workspace files. Query is a regular expression.",
                parameters=object_schema(
                    {"query": {"type": "string"}, "path": {"type": "string"}},
                    ["query"],
                ),
            )
        ]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        args = decode_args(call.input, _SearchArgs)
        if args.query == "":
            raise RuntimeError("query is required")
        path = args.path or "."
        pattern = re.compile(args.query)
        root, _ = resolve_readable(self.workspace, path)
        matches = search_files(self.workspace, root, pattern)
        return "\n".join(limit_lines(matches))


@dataclasses.dataclass(frozen=True, slots=True)
class ApplyPatchTool:
    """Replace expected text in a workspace file."""

    workspace: WorkspaceContext | None = None

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="apply_patch",
                description="Replace old_text with new_text in a workspace file.",
                risky=True,
                parameters=object_schema(
                    {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                        "replace_all": {"type": "boolean"},
                    },
                    ["path", "old_text", "new_text"],
                ),
            )
        ]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        args = decode_args(call.input, _ApplyPatchArgs)
        path, rel = resolve_writable(self.workspace, args.path)
        content = read_file_capped(path, max_read_file_bytes)
        text = content.decode("utf-8", "replace")
        if args.old_text not in text:
            raise RuntimeError("old_text not found")
        count = -1 if args.replace_all else 1
        updated = text.replace(args.old_text, args.new_text, count)
        write_file_no_follow(path, updated.encode("utf-8"), 0o644)
        return "patched " + rel


@dataclasses.dataclass(frozen=True, slots=True)
class WriteFileTool:
    """Write content to a workspace file, creating parent directories."""

    workspace: WorkspaceContext | None = None

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="write_file",
                description="Write content to a workspace file, creating parent directories.",
                risky=True,
                parameters=object_schema(
                    {"path": {"type": "string"}, "content": {"type": "string"}},
                    ["path", "content"],
                ),
            )
        ]

    async def run(self, ctx: RunContext, call: ToolCall) -> str:
        args = decode_args(call.input, _WriteFileArgs)
        path, rel = resolve_writable(self.workspace, args.path)
        os.makedirs(os.path.dirname(path) or ".", mode=0o755, exist_ok=True)
        write_file_no_follow(path, args.content.encode("utf-8"), 0o644)
        return "wrote " + rel


def numbered_lines(content: str, start: int, end: int) -> str:
    """Render ``content`` as ``N: line`` for the requested inclusive range."""
    content = content.removesuffix("\n")
    lines = content.split("\n")
    if start <= 0:
        start = 1
    if end <= 0 or end > len(lines):
        end = len(lines)
    if start > end or start > len(lines):
        return ""
    selected = [f"{number}: {lines[number - 1]}" for number in range(start, end + 1)]
    return "\n".join(limit_lines(selected))


def collect_files(workspace: WorkspaceContext | None, root: str, pattern: str) -> list[str]:
    """List files under ``root``, which the caller already resolved.

    Unreadable entries and symlinks that resolve outside the workspace are
    skipped, not fatal: one outside-pointing symlink (common in node_modules or
    dotfile setups) must not break listing or search for the entire workspace.
    A failure on ``root`` itself is still reported.
    """
    if not os.path.isdir(root):
        # A root that is a regular file is treated as a single entry, and a root
        # that cannot be stat'd is reported as its own error.
        if not os.path.exists(root):
            raise FileNotFoundError(2, "no such file or directory", root)
        rel = _try_readable(workspace, root)
        if pattern and not fnmatch.fnmatchcase(os.path.basename(root), pattern):
            return []
        return [] if rel is None else [rel]

    files: list[str] = []
    root_error: OSError | None = None

    def on_error(error: OSError) -> None:
        nonlocal root_error
        if root_error is None and error.filename == root:
            root_error = error

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=on_error):
        # Directories are read in lexical order and .git is skipped.
        dirnames[:] = sorted(name for name in dirnames if name != ".git")
        for name in sorted(filenames):
            rel = _try_readable(workspace, os.path.join(dirpath, name))
            if rel is None:
                continue
            if pattern and not fnmatch.fnmatchcase(name, pattern):
                continue
            files.append(rel)
    if root_error is not None:
        raise root_error
    files.sort()
    return files


def _try_readable(workspace: WorkspaceContext | None, path: str) -> str | None:
    """The display path when ``path`` is readable, ``None`` otherwise."""
    try:
        _, rel = resolve_readable(workspace, path)
    except (OSError, RuntimeError):
        return None
    return rel


def search_files(workspace: WorkspaceContext | None, root: str, pattern: re.Pattern[str]) -> list[str]:
    """Scan every workspace file under ``root`` for ``pattern``.

    A file that cannot be resolved, opened, or scanned is skipped rather than
    failing the search: one unreadable or over-long file must not hide every
    other match.
    """
    matches: list[str] = []
    for rel in collect_files(workspace, root, ""):
        resolved, display = _resolve_or_none(workspace, rel)
        if display is None:
            continue
        try:
            content = read_file_capped(resolved, max_read_file_bytes)
        except (OSError, RuntimeError):
            continue
        if is_binary(content):
            continue
        for index, raw_line in enumerate(content.split(b"\n")):
            line = raw_line[:-1] if raw_line.endswith(b"\r") else raw_line
            if len(line) > max_search_line_bytes:
                # Scanning stops this file at an over-long line.
                break
            text = line.decode("utf-8", "surrogateescape")
            if pattern.search(text) is not None:
                matches.append(f"{display}:{index + 1}:{text}")
    return matches


def _resolve_or_none(workspace: WorkspaceContext | None, path: str) -> tuple[str, str | None]:
    """``(resolved, display)`` when readable, ``(path, None)`` otherwise."""
    try:
        resolved, display = resolve_readable(workspace, path)
    except (OSError, RuntimeError):
        return path, None
    return resolved, display


def read_file_capped(path: str, limit: int) -> bytes:
    """Read a file, refusing to load more than ``limit`` bytes.

    It opens with ``O_NOFOLLOW`` on platforms that support it so a symlink
    swapped in between containment resolution and the open cannot point the read
    at a file outside the workspace.
    """
    file: IO[bytes] = open_workspace_file(path)
    with file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("not a regular file")
        if info.st_size > limit:
            raise RuntimeError(f"file is too large to read ({info.st_size} bytes)")
        return file.read(limit + 1)


def limit_lines(lines: list[str]) -> list[str]:
    """Cap a line list at ``max_tool_output_lines`` plus a truncation marker."""
    if len(lines) <= max_tool_output_lines:
        return lines
    # A fresh list: appending the marker onto the caller's list would otherwise
    # yield a 201-line result.
    return [*lines[:max_tool_output_lines], "... truncated"]


def is_binary(content: bytes) -> bool:
    """Whether ``content`` contains a NUL byte."""
    return b"\x00" in content
