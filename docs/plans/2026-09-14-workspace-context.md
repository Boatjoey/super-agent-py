# Workspace Context Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add first-version Project resolution and a single WorkspaceContext policy used by local filesystem and command tools.

**Architecture:** The composition root resolves a Project, constructs a workspace Context, and injects it into tools and the session filesystem adapter. Project/config discovery stays distinct from access roots. Runtime machine and engine packages remain unchanged.

**Tech Stack:** Python standard library (`pathlib`, `os`), existing external-package tests, existing tool registry and session ports.

---

### Task 1: Specify project and workspace behavior

**Files:** Modify `docs/architecture.md`, `docs/tools.md`, `docs/session.md`, `docs/config.md`, `README.md`, and `docs/contributing.md`.

Document ownership, project detection, canonical containment, cwd behavior, config/access separation, and the shell sandbox limitation.

### Task 2: Add Project resolution

**Files:** Create `project/project.py`; test in `tests/project/test_project.py`.

Test explicit selection, upward `.git` discovery, and cwd fallback, then implement the resolver.

### Task 3: Add WorkspaceContext

**Files:** Create `workspace/context.py`; test in `tests/workspace/test_context.py`.

Cover relative and absolute paths, traversal, prefix collisions, access modes, additional roots,
outside paths, nonexistent targets, and symlink escapes.

### Task 4: Inject the context into tools and session adapters

**Files:** Modify `tools/files.py`, `tools/commands.py`, `tools/bash.py`, `tools/registry.py`,
`tools/lsp/client.py`, `workspace/workspace.py`, `app/config.py`, `app/session.py`, and `app/subagents.py`.

Replace per-tool cwd/path policy with the context and preserve compatibility constructors only for tests and callers.

### Task 5: Wire startup and verify

**Files:** Modify `__main__.py` and relevant tests under `tests/app`, `tests/tools`, and `tests/workspace`.

Add `--cwd`, prove command cwd injection without changing the process cwd, run focused tests, format,
then run `./scripts/verify.sh`.
