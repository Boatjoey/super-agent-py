# Architecture

Super Agent follows a hexagonal architecture with a state-machine domain core.

```mermaid
flowchart TD
    app["app (composition root)"]
    tui["tui — TUI adapter"]
    adapters["llm / tools / store / project / workspace"]
    port["Conversation port"]
    session["runtime/session"]
    engine["runtime/engine"]
    machine["runtime/machine"]

    app --> tui
    app --> adapters
    tui --> port
    port --> session
    session --> engine
    engine --> machine
```

## Dependency Rule

Dependencies point inward, toward `runtime/machine`. The rule is enforced, not merely
documented: `tests/architecture/dependencies_test.go` parses the imports of every package and fails
the build on a violation.

- `runtime/machine` is the domain core. It owns states, events, runtime-data changes, action plans,
  scheduled actions, and transitions. See `machine.md`.
- `runtime/protocol` owns model and tool adapter contracts (`Message`, `ToolCall`, `Model`,
  `ToolRunner`) without state-machine policy.
- `runtime/permission` owns permission request and command classification value types.
- `runtime/engine` drives the machine. It owns the single agent loop, synchronization,
  scheduled-action draining, and run identity. See `runtime.md`.
- `runtime/execution` implements outbound model, tool, and permission ports.
- `runtime/session` exposes application use cases. It must not contain terminal behaviour, and it must
  not import `os` or `path/filepath` — filesystem access goes through ports.
- `tui` is an inbound adapter. It depends only on its `Conversation` port and display DTOs.
- `app` is the composition root. It creates dependencies and converts runtime values to TUI values.
- `llm`, `tools`, `store`, `project`, and `workspace` are top-level adapters. `llm` and `tools` may
  import `runtime/protocol` but not the root `runtime` facade; `store` and `workspace` may also import
  `runtime/session`, which is where their ports are declared.

`tui` must never import `runtime`, and the runtime must never import `tui`. Runtime states become
presentation-only `tui.AgentStatus` values at the app boundary, in `app/tui_adapter.go`; the TUI owns
no runtime state enum.

The root `runtime` package is a compatibility facade organized by `api_model.go`, `api_machine.go`,
`api_execution.go`, `api_engine.go`, and `api_session.go`. It exposes session persistence ports and
metadata without importing concrete adapters. Internal packages must depend on the narrow package
that owns a type, not on this facade.

The pre-facade names `ToolCallsReceived`, `ToolCallAvailable`, `EventClassifier`, and `ResultResolver`
were intentionally retired in favour of `ToolBatchReceived`, `ToolCallNeedsApproval`, and
`ActionResultResolver`. They are not re-exported; do not reintroduce them.

The TUI is the only interaction surface. Headless CLI, HTTP server, WebSocket, and alternate UI
adapters are out of scope.

## Package Boundaries

`runtime/machine` is the pure domain core. It performs no I/O, takes no locks, and calls no model or
tool. Its file layout:

- `state.go`: the runtime state type and its constants.
- `event.go`: the event interface, event kinds, and `AllEvents`.
- `runtime_data.go`: the complete mutable machine data.
- `runtime_data_change.go`: the runtime-data change vocabulary and `AllRuntimeDataChanges`.
- `runtime_data_change_applier.go`: transactional clone, apply, and validate.
- `action_plan.go`: the post-transition action-queue plan.
- `scheduled_action.go`: the post-commit scheduled-action vocabulary and `AllScheduledActions`.
- `tool_batch.go`: queued tool-batch state.
- `snapshot.go`: snapshot construction and state invariants.
- `transition.go`: the static transition registry and its handlers.

`runtime/engine` is split by responsibility:

- `engine.go`: dependencies and construction.
- `commands.go`: lifecycle, approval, policy, and context commands.
- `action_loop.go`: transition dispatch and scheduled-action draining.
- `query.go`: state queries and immutable snapshots.

Engine files name `machine`, `execution`, and `protocol` types explicitly; the package has no internal
alias facade.

`runtime/execution` implements the ports:

- `scheduled_action_runner.go`: executes scheduled actions, returns `ActionCompletion` values.
- `scheduled_action_executor.go`: calls the model or the tool runner.
- `scheduled_action_result.go`: the result vocabulary.
- `action_queue.go`: the post-commit scheduled-action queue.
- `action_result_resolver.go`: maps results to transition-ready events and classifies tool calls.
- `policy.go`: permission decisions. `command_analyzer.go`: shell inspection and classification.
- `approval_store.go`: always-allow and auto-approve state. `run_controller.go`: run id, cancel
  function, and stale-result checks.

`runtime/session` separates use cases by intent:

- `session.go`: construction, configuration, reset, and snapshots.
- `turn.go`: one conversational turn and the approval flow.
- `history.go`: saved sessions, compaction, and undo.
- `persistence.go`: persistence notifications.
- `notifications.go`: the session-to-UI notification protocol.
- `repository.go`: the persistence and workspace ports, including checkpoint creation,
  `LoadUndoPoint`, `TruncateAfter`, and the one-time `SaveWorkspaceDescription` upgrade.

The TUI's feature ownership, message routing, focus, effects, views, and port rules are specified in
[`tui.md`](tui.md#feature-architecture).

`project` resolves the selected project independently from filesystem access policy. `workspace.Context`
is the process-independent source of truth for workspace roots and cwd, while `workspace.Workspace`
adapts it to the session checkpoint, attachment, export, and workspace-restore ports (including the
one-time canonical upgrade of legacy saved paths). `store/store.go` writes and replays
durable session records — see `session.md` for the durability ordering it maintains. `app/mcp.go`
coordinates MCP lifecycle, dynamic tool registration, rollback, and atomic settings persistence.

## Refactoring Rules

- Prefer concrete domain names over generic plumbing names.
- Keep interfaces at adapter boundaries, not between every internal function.
- Keep files focused on one responsibility.
- Convert transport and display DTOs only at the composition boundary.
- Preserve behaviour with transition and application-use-case tests.
- Do not scatter transition rules into `tui/`, `llm/`, or `tools/`.
- Keep `RunID` stale filtering in the engine. Keep state, call-id, queue guards, and invariants in
  `runtime/machine`.

Use the existing vocabulary: `State`, `RuntimeData`, `Event`, `RuntimeDataChange`, `ActionPlan`,
`ScheduledAction`, `Transition`.
