# WorkspaceSpec Persistence Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist each session's workspace description and restore a freshly validated WorkspaceContext on resume.

**Architecture:** `runtime/session` owns the persistence DTO and lifecycle port, while the concrete `workspace` adapter validates specs and atomically switches the context shared by session filesystem operations and built-in tools. Store metadata maps the DTO without making Project, ConfigRoot, or Workspace roots aliases.

**Tech Stack:** Go standard library, JSON session metadata, existing repository adapter, external-package tests.

---

### Task 1: Define and persist WorkspaceSpec

**Files:** Modify `runtime/session/repository.go`, `runtime/api_session.go`, `store/store.go`, and `store/repository.go`.

Add the persistence DTO, independent project/config fields, JSON mappings, creation, load, list/fork propagation, and round-trip tests.

### Task 2: Add validated runtime activation

**Files:** Modify `workspace/context.go`, `workspace/workspace.go`, and tests under `tests/workspace`.

Rebuild a new context from every saved path, reject missing or redirected roots, use hard failure for every invalid additional root, and atomically switch only after full validation.

### Task 3: Restore Workspace during resume

**Files:** Modify `runtime/session/history.go`, `runtime/session/session.go`, and `tests/runtime/session_store_test.go`.

Validate before mutating the active session, activate the saved workspace, preserve saved ProjectID and ConfigRoot, and provide a saved-cwd-only compatibility path for legacy metadata.

### Task 4: Share the switchable workspace with tools

**Files:** Modify `app/session.go`, `app/subagents.go`, `tools/sandbox.go`, `tools/sandbox_linux.go`, `tools/sandbox_other.go`, and `tools/lsp/client.go`.

Inject one runtime workspace binding into session and tools, derive each command sandbox invocation from its current primary root, and reconnect LSP clients after a restored cwd change.

### Task 5: Verify lifecycle behavior

**Files:** Add or modify tests under `tests/runtime`, `tests/store`, `tests/app`, `tests/tools`, and `tests/workspace`.

Cover persistence, changed process cwd, access modes, invalid/missing/symlink-replaced paths, ConfigRoot independence, legacy sessions, command cwd after activation, and complete repository verification.
