# Session and Context

`runtime/session` exposes application use cases and owns the boundary between the runtime and the
interface. It never contains terminal behaviour and never schedules actions — see `runtime.md` for what
it hands to the engine.

This file covers what the agent "remembers": how context is assembled, persisted, and mutated.

## What Context Is

There is no vector database and no cross-session long-term memory. Everything the agent can recall is
the `[]Message` handed to the model, held in `runtime/machine.RuntimeData.Messages`.

`Message` (`runtime/protocol/types.py`) has four roles:

| Role | Content |
|---|---|
| `system` | Built-in prompt and project instructions |
| `user` | The user's question, optionally with attachments |
| `assistant` | Model output, including reasoning content and tool calls |
| `tool` | A tool result, linked to its call by `ToolCallID` |

Every model call passes the complete message list and the current tool definitions:
`Messages + ToolSpecs -> Model.Next -> ModelResponse`. Whether the model "remembers" something is
therefore exactly whether it is still in `Messages`.

## Initial Context

At startup, the application resolves a `Project` first. An explicitly selected directory wins;
otherwise resolution walks from the process cwd toward the filesystem root and chooses the first
directory containing `.git`, falling back to the process cwd. In this first version the application
creates a workspace whose primary root and cwd are both the project root. These remain separate values
so additional access roots, worktrees, and child-specific workspaces do not require redefining a
project.

Workspace access roots and instruction/config roots are separate concepts. Adding a readable or
writable workspace root grants file access only; it never causes `AGENTS.md`, `CLAUDE.md`, hooks,
skills, plugins, or configuration to be loaded from that root. Project instruction discovery uses the
selected project root as its configuration root.

`WorkspaceSpec` is the durable description of the workspace: primary root, cwd, and roots with their
access modes. It contains no authorization behaviour. `workspace.Context` is reconstructed from that
description and performs canonicalization, symlink validation, containment, and read/write decisions.
New session metadata persists `ProjectID`, `ConfigRoot`, and `WorkspaceSpec` as independent fields;
none is derived from another during replay.

`app.NewSession` builds one `system` message from:

```text
app.SystemPrompt
  + user-level ~/.superagent/AGENTS.md
  + project instructions from the repository root down to the working directory
```

`app/instructions.Load` applies these rules:

1. Read the optional user-level spec at `~/.superagent/AGENTS.md`.
2. Then read project instructions in root-to-leaf order, merging as it descends.
3. In a directory, a non-empty `AGENTS.md` wins; `CLAUDE.md` is loaded as lower-priority compatibility
   guidance only when that directory has no `AGENTS.md`.
4. Each instruction file is capped at 128 KiB. An oversized file aborts loading with a clear
   path-specific error rather than being silently truncated.

All sources are merged into a single `system` message, not re-scanned each turn. Session metadata also
records the instruction source paths and a fingerprint of the system message, so a session's original
environment can be identified later.

## How Context Grows

A plain answer appends two messages:

```text
system + history
  -> append user message
  -> CallModel
  -> append assistant message
```

A tool call turns this into a loop:

```text
assistant(tool_calls)
  -> approve and execute tools
  -> tool(result)
  -> hand the updated Messages back to the model
  -> final answer, or another tool call
```

Tool results are not hidden memory; they are ordinary `tool` messages. A failing tool is converted into
a tool result beginning with `Error:`, and a denied call into `denied: <tool-name>` or
`denied by permission policy: <reason>`, so the model can react instead of losing the turn. Approval
decisions are persisted for audit but are never sent to the model.

During streaming, output accumulates in `StreamingContent` and `StreamingReasoning` for live display.
Only the committed final response becomes a history message.

## Runtime Versus Persisted

Context exists at two levels:

- `RuntimeData.Messages` is the live truth the model is called with.
- `~/.superagent/sessions/<session-id>/events.jsonl` is the durable event log used to rebuild history.

`runtime/session/snapshot_emitter.py` watches engine snapshots, forwards newly appended messages to the
TUI, and persists them through `Repository`. It tracks how far it has emitted so nothing is written
twice.

```text
~/.superagent/sessions/<session-id>/
  meta.json      # id, title, timestamps, provider, model, cwd, current turn id,
                 # parent id, instruction fingerprint, instruction sources
  events.jsonl   # messages, tool results, approvals, cancel, reset, compaction,
                 # checkpoints, context replacement
```

The event log holds more than the model context. On replay, `store.messagesFromRecords` converts only
five record types into `Messages`: appended messages, tool results, reset, compaction, and context
replacement. Approvals, errors, and cancels exist for audit and never enter the context.

Creation writes the transcript first and `meta.json` last, so an interrupted creation cannot leave an
orphan session that looks complete.

## Turning a Session: `/resume`

`Session.Resume` loads the event log and saved workspace through `Repository.Load`. Before changing the
active session it validates every saved workspace root and cwd against the current filesystem and
reconstructs a fresh context. Missing, moved, or symlink-replaced primary roots, missing additional
roots, and a cwd outside the restored roots are hard failures reported as `saved workspace is no
longer valid`; resume never falls back to the process cwd or the current `--cwd`. The complete
workspace is activated only after validation succeeds, so built-in file, command, and LSP tools share
the restored context.

Sessions written before `WorkspaceSpec` carry only a saved metadata `cwd`, which was never promised to
be canonical. On the first resume the session upgrades that metadata exactly once: it resolves the
saved `cwd` against the current filesystem, validates the result, and persists the canonical
description before mutating anything. Every later resume then takes the strict path above, so the
lenient upgrade never becomes a permanent fallback. An old session without a saved cwd cannot be
resumed. The upgrade uses the old session's data, never current process state, and it leaves
`ProjectID`, `ConfigRoot`, and other metadata untouched.

A session that already carries a `WorkspaceSpec` is never upgraded. If its saved spec no longer
validates, the resume fails instead of falling back to `cwd`. If the canonical upgrade cannot be
persisted, the resume fails rather than report a migration that did not happen.

After workspace validation, `Session.Resume` rebuilds `Messages` and calls
`Engine.ReplaceMessages`, which:

- cancels the current run so late results are dropped;
- clears pending approval, current tool, tool batch, and streaming buffers;
- returns the state to `Idle`;
- continues later turns from the restored history.

`/resume` restores persisted *messages*. It does not resume a half-executed tool call.

`ConfigRoot` remains independent on resume. Restoring workspace access does not scan any workspace
root for instructions, hooks, skills, plugins, or project configuration. This phase preserves the
saved config-root identity but does not hot-reload extension configuration while switching sessions.

Application storage such as `~/.superagent/sessions`, logs, caches, and a future centrally managed
worktree area is not a workspace root and does not require workspace authorization. The existing
delegate worktree path is currently created inside the user workspace, so its write check remains
appropriate; moving that facility into application storage would require a separate storage adapter,
not an additional workspace root.

## Compacting: `/compact [summary]`

`Session.Compact` shrinks the model context to "a summary plus the most recent messages":

1. If no summary is supplied, make one extra model call over the full history to produce it.
2. Preserve every `system` message.
3. Replace older non-`system` messages with one `system` message: `Conversation summary:\n...`.
4. Keep the most recent 4 non-`system` messages by default.
5. Persist the compaction record first, then replace the engine's messages, so memory and disk cannot
   diverge.

The record keeps both `OriginalMessages` and `KeptMessages`; a later resume uses `KeptMessages`.
Compaction is lossy — details the summary does not cover are no longer sent to the model.

## Resetting

`Session.Reset` writes a `reset` event before calling `Engine.Reset`. The machine's `ResetConversation`
runtime-data change drops every non-`system` message and keeps all `system` messages:

```text
before: system + user + assistant + tool + ...
after:  system
```

Replay applies the same rule, so a restart does not resurrect a reset conversation while project
instructions survive it.

## Undo: `/undo`

`/undo` is not "delete the last message". Before a trackable write (`write_file`, `apply_patch`,
`format`), the session captures file snapshots through `Workspace` and stores a checkpoint.

Undo then:

1. finds the most recent **non-empty** checkpoint, skipping empty ones;
2. restores the filesystem first;
3. atomically truncates `events.jsonl` to that checkpoint;
4. replaces the engine context with the messages from before it.

Files are restored before the transcript is truncated: if restoration fails, the history has not been
lost. The result is that the workspace and the model's context return to the state they shared before
the operation.

## Cancel and Late Results

Cancelling stops the current run, clears the tool queue, and returns to `Idle`, keeping messages
already produced. Results that arrive after a cancel, reset, or context replacement are filtered by
`RunID`, so they cannot contaminate the new context.

## Attachments

`/attach <path>` queues a workspace attachment for the next turn and `/attachments` lists the queue.
Attachments are bounded at 10 MiB. Images are sent as native multimodal blocks; supported documents are
sent as file or document blocks. The queue is consumed when the turn starts.

## Delegation

The `delegate` tool creates a persistent child session, runs the selected agent profile, and returns the
child's final response to the parent tool call. Cancellation propagates through the parent context.
Setting `worktree` creates a detached Git worktree under `.super-agent/worktrees/`; the reported path is
retained for inspection.

`/fork [title]` branches the current transcript into a new child session and selects it.

## Limitations

Worth knowing, because they are easy to assume otherwise:

- Every model call sends all of `Messages`. The project does not tokenize and does not auto-compact
  before a limit.
- `/compact` is user-triggered; summary quality depends on the model or the text you supply.
- Persistence is per-session. A new session does not retrieve other sessions.
- Instructions load when a session is created. Resuming an old session uses the `system` message
  persisted at the time; it does not silently adopt newer instructions on disk.
- Checkpoints cover the known write tools whose paths can be resolved. This is not a general
  filesystem snapshot.

## Code Map

| Topic | Location |
|---|---|
| Messages and the model interface | `runtime/protocol/types.py` |
| Initial system message | `app/system_prompt.py`, `app/session.py` |
| Layered instruction loading | `app/instructions/__init__.py` |
| Message mutation and reset | `runtime/machine/runtime_data_change_applier.py` |
| Conversation and tool transitions | `runtime/machine/transition.py` |
| Model and tool execution | `runtime/execution/scheduled_action_executor.py` |
| Turn and message emission | `runtime/session/turn.py`, `runtime/session/snapshot_emitter.py` |
| Resume, compaction, undo | `runtime/session/history.py` |
| Persistence adapter | `store/repository.py`, `store/store.py` |
| File checkpoints | `runtime/session/checkpoint.py`, `workspace/workspace.py` |
