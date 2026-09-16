# Workspace Context Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add first-version Project resolution and a single WorkspaceContext policy used by local filesystem and command tools.

**Architecture:** The composition root resolves a Project, constructs a workspace Context, and injects it into tools and the session filesystem adapter. Project/config discovery stays distinct from access roots. Runtime machine and engine packages remain unchanged.

**Tech Stack:** Go standard library (`filepath`, `os`), existing external-package tests, existing tool registry and session ports.

---

### Task 1: Specify project and workspace behavior

**Files:** Modify `docs/architecture.md`, `docs/tools.md`, `docs/session.md`, `docs/config.md`, `README.md`, and `docs/contributing.md`.

Document ownership, project detection, canonical containment, cwd behavior, config/access separation, and the shell sandbox limitation.

### Task 2: Add Project resolution

**Files:** Create `project/project.go`; test in `tests/project/project_test.go`.

Test explicit selection, upward `.git` discovery, and cwd fallback, then implement the resolver.

### Task 3: Add WorkspaceContext

**Files:** Create `workspace/context.go`; test in `tests/workspace/context_test.go`.

Cover relative and absolute paths, traversal, prefix collisions, access modes, additional roots,
outside paths, nonexistent targets, and symlink escapes.

### Task 4: Inject the context into tools and session adapters

**Files:** Modify `tools/files.go`, `tools/commands.go`, `tools/bash.go`, `tools/registry.go`,
`tools/lsp/client.go`, `workspace/workspace.go`, `app/config.go`, `app/session.go`, and `app/subagents.go`.

Replace per-tool cwd/path policy with the context and preserve compatibility constructors only for tests and callers.

### Task 5: Wire startup and verify

**Files:** Modify `main.go` and relevant tests under `tests/app`, `tests/tools`, and `tests/workspace`.

Add `--cwd`, prove command cwd injection without changing the process cwd, run focused tests, format,
then run `go test ./...` and the repository verification script if practical.
