# Contributing

## Doc-First Workflow

This project treats documentation as the specification for the code. The order matters:

1. **Change the document first.** Decide the behaviour in the owning document under `docs/`.
2. **Implement it.** The code now has a written target to match.
3. **Ship both together.** A behaviour change that `docs/` does not reflect is incomplete.

If the code and a document disagree, treat the code as wrong until the document is deliberately
amended. Restating a normative fact in a second place is not documentation — it is a future
inconsistency. Link instead; see the index in [README.md](README.md).

Two tests enforce the parts that machines can check:

- `tests/architecture/test_spec.py` pins `machine.md` to the real transition graph, both directions.
- `tests/architecture/test_dependencies.py` pins the dependency rule by parsing imports.

## Tests

- All test code lives under `tests/`. Do not add test modules inside production package directories.
- Name tests by behaviour, for example `test_tool_call_feeds_result_back_to_model`.
- Runtime changes should cover transitions and observable engine behaviour.
- Transition tests assert the complete order of runtime-data changes, action-queue changes, and
  scheduled actions.
- Reset tests prove `system` messages are preserved.
- Tests that parse documents or source must fail loudly when the format changes, rather than silently
  matching nothing.
- Shared fakes live in `tests/fakes/`; helper programs that a test spawns as a subprocess live in
  `tests/helpers/` and are run as `python -m tests.helpers.<name>`.

Run everything with `./scripts/verify.sh`, which runs formatting, linting, strict typing, the tests,
the concurrency subset under `python -X dev`, and coverage. GitHub Actions runs it on every push and
pull request.

## Build

`./scripts/build-local.sh` builds the project and installs `super-agent` into `/usr/local/bin`. Set
`SUPER_AGENT_INSTALL_DIR` to override the install directory and `SUPER_AGENT_VENV` to override the
virtual environment the console script runs from. `SUPER_AGENT_BINARY_NAME` overrides the installed
name.

## Acceptance

`./scripts/verify.sh` covers everything a machine can check quickly. Two further gates exist because
they need a real terminal or a real process boundary:

`uv run python scripts/smoke.py` drives the installed `super-agent` inside a pseudo-terminal against
a local HTTP server that speaks the OpenAI chat-completions streaming protocol. Nothing touches the
network: only the model provider is faked. It checks, in order:

1. `-h` prints usage to stderr and exits `0`, and the usage does not claim a false flag defaults true;
2. an unknown flag exits `2` with `flag provided but not defined: -x`;
3. an invalid approval mode, and `--yolo` together with `--approval-mode`, both exit `1`;
4. a missing provider credential exits `1` and names the variable to set;
5. a prompt produces a streamed reply in the transcript;
6. a window resize mid-session redraws within the new width;
7. Ctrl+C during a turn cancels it and leaves the loop usable, while Ctrl+C with nothing in flight
   quits;
8. `/clear`, `/compact`, and `/undo` each run to completion and leave the session usable.

Two properties the smoke test deliberately does not assert, and where the assertion lives instead:
**transcript surgery** (`/compact` keeping the newest turns and widening a leading tool result,
`/undo` restoring files before truncating history, a reset keeping `system` messages) is pinned by
the session tests, which can inspect the result precisely; and **interaction and rendering** are
pinned by Textual pilot tests at fixed terminal sizes. They cover focus, submission, viewport
scrolling, modal approval, and terminal-width layout. The smoke test only proves those paths are
reachable and non-destructive through the real interface.

What remains genuinely manual, and why: colour and layout legibility; Linux terminal, tmux, and SSH
behaviour; bracketed paste; Unicode and wide glyphs; terminal-native full-screen selection and copy;
terminal-dependent key sequences (`Shift+Enter`, `Alt+Enter`, `Ctrl+J`); and clipboard fallback. A
visible TUI change must record the tested terminals in its pull request.

## Concurrency

Python's single-threaded event loop rules out unsynchronised access to shared memory, but it
introduces a different failure mode — state that is only safe because nothing yields between reading
it and writing it. The approximations in use are:

- `python -X dev`, run over the concurrent test directories by `./scripts/verify.sh`. It enables
  asyncio debug mode and turns un-awaited coroutines, unclosed transports, and slow callbacks into
  visible failures.
- `pytest-asyncio` in `strict` mode, so every test gets a fresh event loop and a coroutine is never
  accidentally shared between tests.
- A deliberate-yield stress harness, `tests/runtime/test_concurrency_stress.py`. Every port call in
  it yields several times before answering, and unrelated coroutines read snapshots and cancel while
  a turn is in flight, so the loop takes every opportunity to interleave. It repeats each scenario
  enough times to reach more than one interleaving, and asserts the exact transcript, the exact final
  state, and that the state observer never runs with the lock held. It is an approximation: it widens
  the window a bug needs, it does not close it.

These are approximations. A synchronisation bug that needs a specific interleaving to show up can
still escape, which is the residual gap. When you touch the engine loop, the run controller, the
action queue, or the session emitter, say in the pull request what you did to convince yourself the
commit points are still atomic.

## Python Conventions

The style is deliberate and consistent across the codebase:

- **Naming follows PEP 8.** Modules, functions, and methods are `snake_case`; classes are
  `PascalCase`; module constants and enum members are `UPPER_SNAKE`; dataclass fields are
  `snake_case`. So `STATE_IDLE`, `AppendUserMessage`, `run_turn`, and `apply_runtime_data_changes`
  all follow it.
- **Module names are snake_case**: `runtime/machine/transition.py`.
- **A package re-exports its surface from `__init__.py`**, so `machine.STATE_IDLE` and
  `machine.transition` resolve from the package namespace.
- **Value types are `@dataclass` with an explicit codec** (`super_agent/jsonutil.py`), not a
  validation library. Each field declares its JSON key with `json_field`, so the key names stay
  stable regardless of the Python attribute name.
- **Sequence fields** on frozen types are tuples and on mutable types are lists. Frozen means frozen:
  a frozen value type never shares a mutable container with a caller.
- **Ports are `typing.Protocol`**, declared at the adapter boundary and nowhere else. Add
  `@runtime_checkable` only where an `isinstance` check really happens.
- **Sealed type sets** (events, runtime-data changes, scheduled actions) use a base class whose
  `__init_subclass__` refuses a subclass declared outside its own module, and a `kind` class
  variable for the registry key.
- **Errors**: `raise X(...) from err` for wrapping, `super_agent.errors.errors_is` for identity, and
  `JoinedError` for aggregation. Adapters convert `asyncio.CancelledError` into `Cancelled` at their
  boundary.

### Disk-Format Stability

Sessions, settings, memory, and telemetry are persisted artefacts, so their formats are a contract,
not an implementation detail. The rule is **field-level stability**: key names, enum strings,
timestamp format, file modes, and replay semantics must not change, and JSON written by one version
must stay parseable by another.

Timestamps are RFC 3339 with nanosecond precision. Session and turn identifiers come from
`time.time_ns()` formatted by hand, because `strftime("%f")` only reaches microseconds.

Three spellings of the configuration directory exist and none of them may be unified:
`~/.superagent/` (home, no hyphen), `<cwd>/.superagent/` (project extensions, no hyphen), and
`<workspace>/.super-agent/` (worktrees and exports, **with** a hyphen).

## Git and Pull Requests

- Use concise conventional commit messages, for example `fix: preserve reasoning replay`.
- Name branches by scope: `feat/session-notifications`, `fix/tool-approval`.
- A pull request should state its purpose, the main files changed, test output, and any local config
  notes.
- Add screenshots only for visible TUI changes.

## Repository Notes

`CLAUDE.md` is a symlink to `AGENTS.md`, so the two names refer to one file. Edit either; there is
nothing to keep in sync.

## Keeping Documentation Current

- Update `AGENTS.md` when project rules, architecture, commands, tests, or security guidance change.
- Update the owning document under `docs/` when behaviour changes — before the code, per the workflow
  above.
- When a fact moves, delete the old copy. Two copies of a fact drift.

## Project Layout at a Glance

```text
super_agent/__main__.py      entry point and console script
app/                         composition root and configuration
app/instructions/            layered instruction loading
runtime/machine/             pure domain core — states, transitions, invariants
runtime/engine/              orchestration, the single agent loop
runtime/execution/           model, tool, and permission ports
runtime/session/             application use cases and ports
runtime/protocol/            adapter contracts
runtime/permission/          permission vocabulary
runtime/telemetry/           JSONL telemetry
tui/                         Textual inbound adapter and feature-owned widgets
llm/                         provider adapters
tools/                       file, command, git, web, MCP, and LSP tools
store/                       durable session storage
project/                     project root resolution
workspace/                   workspace access policy and filesystem session adapter
tests/                       tests by module, plus shared fakes and helpers
docs/                        this specification
```

Each package's files and responsibilities are listed in [architecture.md](architecture.md).
