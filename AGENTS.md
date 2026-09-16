# Repository Guidelines

Development rules for this repository. User-facing usage, command reference, and behaviour
specifications live in `docs/` — see the index at `docs/README.md`.

## Essentials

- Python project (3.12): agent runtime, LLM adapters, local tools, Rich TUI. Distribution `super-agent-py`, import root `super_agent`, console script `super-agent`.
- Design pattern: hexagonal architecture with a functional core and imperative shell. `runtime/machine` is the pure domain core; engine, session, TUI, LLM, tools, and store are ports or adapters around it.
- State-machine flow is `Event -> validated MachineSnapshot -> Transition -> RuntimeDataChange + ActionPlan -> transactional RuntimeDataChangeApplier/Executor -> ActionResultResolver -> Event`; dependencies point toward the machine.
- Keep `RunID` stale filtering in the engine. Keep state, call-id, queue guards, and invariants in `runtime/machine`.
- RuntimeDataChangeAppliers must clone, apply, and validate runtime data; the engine commits runtime data and the transition's action plan under one lock only after validation.
- The engine owns the single agent loop and notifies a per-turn state observer after state-changing transitions; the session supplies approval, streaming, notification, and persistence ports without scheduling actions.
- Start turns through `Engine.RunTurn`; route other external machine events through `Engine.DispatchEvent`. Only `UserMessageSubmitted` starts a run, and `AwaitApproval` keeps approval waiting inside the scheduled-action loop.
- Keep state-machine logic in `runtime/machine/transition.py`; transitions use one package-private static registry keyed by state and event kind.
- Keep state definitions in `runtime/machine/state.py`, complete machine data in `runtime/machine/runtime_data.py`, runtime-data changes in `runtime/machine/runtime_data_change.py`, action plans in `runtime/machine/action_plan.py`, and tool-batch data in `runtime/machine/tool_batch.py`.
- Keep orchestration in `runtime/engine/`; constructors belong in `engine.py`, commands in `commands.py`, scheduled-action draining in `action_loop.py`, and queries in `query.py`.
- In `runtime/engine`, reference `machine`, `execution`, and `protocol` owners explicitly; do not re-export them through internal aliases.
- Keep scheduled-action execution in `runtime/execution/`.
- Map scheduled-action results directly to transition events with `runtime/execution.ActionResultResolver`.
- Keep session/UI boundary in `runtime/session/`.
- Use `SessionNotification` for runtime-session output and convert it to `tui.ConversationNotification` at the app boundary; reserve `machine.Event` for state-machine input.
- Keep turn I/O wiring in `runtime/session/turn.py` and history use cases in `runtime/session/history.py`.
- Keep storage and filesystem access behind `runtime/session.Repository` and `runtime/session.Workspace`.
- Follow the feature-oriented TUI boundaries in `docs/tui.md`; keep views pure and ports feature-local.
- Map runtime states to presentation-only `tui.AgentStatus` values in `app/tui_adapter.py`; TUI must not define runtime state enums.
- Keep durable session storage in `store/` and filesystem checkpoint access in `workspace/`.
- Load layered instructions with `app/instructions`: user-level spec, root-to-leaf `AGENTS.md`, fallback `CLAUDE.md`.
- Preserve `system` messages such as project instructions across reset.
- Do not scatter transition rules into `tui/`, `llm/`, or `tools/`.
- Use existing vocabulary: `State`, `RuntimeData`, `Event`, `RuntimeDataChange`, `ActionPlan`, `ScheduledAction`, `Transition`.
- Follow the hexagonal architecture in `docs/architecture.md`; `tui` must not import `runtime`.
- Keep the TUI as the only interaction surface; do not add headless, server, or alternate UI entry points.
- LLM and tool adapters may import `runtime/protocol`, not the root `runtime` facade.
- MCP stdio adapters live in `tools/mcp`; discovered tools join `tools.Registry` atomically and remain risky under the common permission policy.
- `app.MCPController` coordinates MCP lifecycle, dynamic registry changes, and atomic settings persistence; TUI only calls its application-facing adapter.
- Go exported identifiers keep their Go spelling (`StateIdle`, `AppendUserMessage`, `RunTurn`) so the two implementations can be read side by side; module names are snake_case.
- Concurrency is asyncio: `context.Context` maps to a per-run `RunContext` plus an `asyncio.Task`, goroutines to tasks, channels to `asyncio.Queue`, and subprocesses to `create_subprocess_exec`. Adapters convert `asyncio.CancelledError` into the project's `Cancelled` at their boundary; `CancelledError` is a `BaseException`, so it passes through `except Exception`.
- Adapters translate third-party SDK values into `runtime/protocol` types at the boundary; SDK objects never reach the machine.

## Documentation

- Documentation is the specification for the code, not a description of it. Change the relevant `docs/` file first, then the code, and ship both in the same change.
- A behaviour change that `docs/` does not reflect is incomplete, even when the code works.
- When code and a document disagree, the code is wrong until the document is deliberately amended.
- Every normative fact has exactly one home in `docs/`; link to it rather than restating it. Duplicated facts drift, and prior duplicates in this repository had already diverged.
- `docs/README.md` indexes the document set; `docs/contributing.md` covers the workflow, tests, and git conventions.
- `tests/architecture/test_spec.py` enforces `docs/machine.md` against the real transition graph; `tests/architecture/test_dependencies.py` enforces the dependency rule.
- Keep `AGENTS.md` to development rules. Usage, command reference, keybindings, and behaviour specs belong in `docs/`, not here.

## Commands

- `uv run super-agent`: run the TUI. Flags are listed in `README.md`.
- `uv run pytest`: run all tests.
- `uv run ruff format <files>`: format changed Python files.
- `./scripts/coverage.sh`: run tests with whole-project coverage.
- `./scripts/verify.sh`: run formatting, lint, strict typing, tests, and coverage.
- `./scripts/build-local.sh`: install `/usr/local/bin/super-agent`.

## Tests

- All test code must live under `tests/`.
- Do not add test modules inside production package directories.
- Name tests by behaviour, for example `test_tool_call_feeds_result_back_to_model`.
- Runtime changes should cover transitions and observable engine behavior when practical.
- Transition tests should assert complete runtime-data-change/action-queue-change/scheduled-action order.
- Reset tests should prove system messages are preserved.
- Tests that parse documents or source must fail loudly when the format changes rather than silently matching nothing.
- GitHub Actions runs `./scripts/verify.sh` for pushes and pull requests.
- There is no `-race` equivalent: `python -X dev`, `PYTHONASYNCIODEBUG=1`, strict `pytest-asyncio`, and a deliberate-yield stress loop approximate it. `docs/contributing.md` records the residual gap.

## Security

- Do not commit secrets.
- `YOLO=true` in `.env` enables bypass only when no explicit `--approval-mode` flag was passed; the flag always wins over the environment, so a checked-in `.env` cannot silently disable permission prompts.
- Command classification routes approvals but is not a security boundary.
- Linux command tools use strict bubblewrap isolation by default with a read-only host root, a writable workspace, policy-controlled networking, ephemeral home/tmp, and `prlimit` resource bounds.
- Strict sandbox mode fails closed when `bwrap` or `prlimit` is unavailable; unsupported platforms require an explicit `sandbox.mode: off`.
- `web_search` and `browser_fetch` are risky network tools; browser fetch blocks local and private targets and enforces redirect, timeout, and response limits.
- Discovered MCP tools are always risky under the common permission policy.
- Extension tool hooks must use recursion-safe direct execution.
- Configuration locations, keys, and permission rules are specified in `docs/config.md`.
