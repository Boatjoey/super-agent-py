# Runtime

`runtime/engine` drives the machine. It owns the single agent loop, the state lock, run lifecycle,
scheduled-action draining, and stale-result dropping. `runtime/execution` implements the outbound
model, tool, and permission ports that the loop calls.

State definitions and the transition graph live in `machine.md`; this file covers how a transition is
applied and how the loop keeps going.

## The Runtime Cycle

```text
QueuedAction { run_id, action_id, action }
  -> ScheduledActionRunner.run -> ActionCompletion
  -> stale RunID check
  -> ActionResultResolver.resolve -> transition Event
  -> snapshot_from(runtime_data) -> validated MachineSnapshot
  -> transition(snapshot, event)
  -> TransitionResult { next_state, runtime_data_changes, action_plan }
  -> RuntimeDataChangeApplier.apply_runtime_data_changes on cloned RuntimeData -> validate_runtime_data
  -> atomic RuntimeData + ActionPlan commit
  -> ScheduledAction executed
```

The shape is a cycle: an action produces a result, the result resolves to an event, the event drives a
transition, and the transition schedules the next action.

Four stages carry the semantics worth knowing:

- `snapshot_from` validates the complete runtime data and exposes only the guards a transition needs.
- `transition` decides the next state, the runtime-data changes, and the action plan. It is pure.
- `RuntimeDataChangeApplier` clones runtime data, applies the changes, and validates the result. A
  candidate that fails validation is never committed, so an invalid intermediate state cannot exist.
- The engine commits the runtime data **and** the action plan under one lock, and only then runs the
  plan. `ActionPlan.clear_existing` retracts obsolete queue work in the same atomic step that schedules
  the replacement, so there is no window where the queue belongs to a state that no longer exists.

Scheduled actions run only after that commit. Nothing observes an action running against runtime data
that was not yet committed.

## The Single Loop

`runtime/engine/action_loop.py` holds the only agent loop, `_run_scheduled_actions`. It pops from the
action queue until the queue is empty, and the run ends only when the queue is empty *and* the state
is `Idle`:

```python
run_id = self._runs.current_run_id()
while True:
    async with self.lock:
        action = self._action_queue.pop()
        if action is None:
            if self._runtime_data.state == STATE_IDLE:
                self._runs.finish_run(run_id)
                return
            state: State = self._runtime_data.state
            raise InvariantViolationError(f"action queue is empty in state {state}")
    # execute, resolve, transition, commit, repeat
```

An empty queue in an active state is an invariant violation, not a quiet stop: every non-`Idle` state
must have pending or in-flight work. The real loop also handles locking, finishing and cancelling runs,
`RunID` filtering, error transitions, and state notification.

`_execute_scheduled_action` wraps each iteration: run the action, resolve the result into an event,
compute the transition, commit it atomically, and let the new actions enter the queue.

## Scheduled Actions

Four actions exist, enumerated by `machine.ALL_SCHEDULED_ACTIONS` and defined in `machine.md`:
`CallModel`, `RunTool`, `CheckToolQueue`, and `AwaitApproval`.

Model calls, tool execution, and human approval all travel the same action → result → event path. That
is why approval needs no second loop: `AwaitApproval` waits on a port the Session injects, but the
engine still owns the scheduling.

## Action Results

`ActionResultResolver` maps model and tool results directly to events the transition table accepts. It
starts tool batches and classifies each queued call into `ToolCallNeedsApproval`,
`ToolCallReadyToRun`, or `ToolCallDenied`.

A batch is the context unit; a call is the approval and execution unit. A denial — from the user or
from the policy — is appended as that call's tool result and queue processing continues, so the model
can choose another action instead of losing the turn. The two denial paths and their result text are
listed in `machine.md`.

`runtime/execution` owns command classification, protected-path checks, network default-deny
behaviour, and structured permission requests. Command classification routes approvals; it is **not** a
security boundary. What actually contains a command is the sandbox, described in `tools.md`.

## Session's Role

`runtime/session` starts a turn and supplies ports; it never schedules actions. `runtime/session/turn.py`
provides exactly four things to the engine:

- an `ApprovalWaiter`, which reads the approval channel and persists each decision;
- an `onStreamChunk` callback, which forwards streaming output as notifications;
- a state observer, registered per turn, which converts engine snapshots into `SessionNotification`
  values;
- a usage observer, registered per turn, which reports what each completed model call of the current
  run cost as a `UsageReported` notification. A response the adapter could not measure reports nothing.

The state observer is how the TUI follows states that pass *between* snapshot points, such as
`RunningTool` while a tool executes. It runs outside the engine lock so it can read snapshots safely.

The usage observer carries the provider's own token counts, so the interface can show context usage
without tokenizing the conversation itself. Both observers are installed for one turn and cleared when
it ends.

This keeps the dependency direction intact: Session starts the use case, Engine owns the loop,
Execution performs the work, and Machine decides the transitions.

## Run Lifecycle and Staleness

`Engine.run_turn` starts a run; it is the only entry point that accepts `UserMessageSubmitted`. Every
other external machine event goes through `Engine.dispatch_event`, which rejects a user message outright
so a turn cannot be started by accident.

Each run has a `RunID`. `RunController` owns the id, the cancel function, and the liveness check. When
an action completes, the engine drops the result if its `RunID` is no longer current. Stale results
appear whenever a run is cancelled, reset, or replaced while a model or tool call is still in flight;
dropping them is what stops a cancelled turn from writing into the next one.

The loop ends in one of five ways:

| Situation | Outcome |
|---|---|
| `Idle` and the queue is empty | Normal completion, run finishes |
| An action returns an error | `ErrorOccurred` is submitted, then the error returns |
| The context is cancelled, or approval input breaks | `CancelRequested` is submitted, then it returns |
| A `RunID` is stale | The late result is dropped and the loop continues |
| The queue is empty in a non-`Idle` state | `InvariantViolationError` |

## Telemetry

`runtime/telemetry` writes synchronized JSON Lines records for transitions, actions, and runs. Records
carry run and action correlation IDs, the tool component name, duration, errors, and estimated model
token counts. `telemetry.log_path` selects the destination. Token figures are estimates — the project
does not tokenize; see the limitations in `session.md`.

## Terms

- `Engine`: unified external event dispatch, the single agent loop, the action queue, the state lock,
  run lifecycle, scheduled-action draining, and stale dropping.
- `ActionQueue`: stores post-commit scheduled actions.
- `ActionResultResolver`: turns a scheduled-action result into a transition-ready event and applies
  tool policy.
- `ScheduledActionRunner`: executes scheduled actions and returns `ActionCompletion` values.
- `RunController`: owns the run id, the cancel function, and stale-result checks.
- `ApprovalStore`: stores always-allow and auto-approve state.
- `Policy`: permission mode, allow and deny rules, command classification, and the approval decision.
- `Session`: the channel boundary supplying approval input, streaming output, notifications, and
  persistence without scheduling actions.

Machine-side terms — `State`, `Event`, `RuntimeData`, `RuntimeDataChange`, `ActionPlan`,
`ScheduledAction`, `MachineSnapshot`, `transition` — are defined in `machine.md`.

## Reading Order

To follow one turn end to end, read in this order:

```text
runtime/session/turn.py
  -> runtime/engine/action_loop.py
  -> runtime/execution/scheduled_action_runner.py
  -> runtime/execution/scheduled_action_executor.py
  -> runtime/execution/action_result_resolver.py
  -> runtime/machine/transition.py
```

In one sentence: `transition` decides the next step, the engine's single action loop drives it forward,
and the Session only connects user input to notifications.
