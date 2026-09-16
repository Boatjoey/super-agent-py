# Super Agent

Python agent runtime with a state-machine core, LLM providers, local tools, and a Rich TUI.

![TUI screenshot](./static/ui.png)

## Quick Start

```bash
uv run super-agent
```

The TUI is the only interaction surface. On first run the app writes a settings template to
`~/.superagent/settings.json`; add your provider API key there before the first prompt.

Common flags:

- `--no-tools` — disable tool calling
- `--yolo` — auto-approve tool execution
- `--approval-mode <ask|accept-edits|plan|bypass>` — choose the permission mode
- `--cwd <directory>` — explicitly select the project directory

To build and install the console script:

```bash
./scripts/build-local.sh
```

## Development

```bash
uv sync                 # install dependencies
uv run pytest           # run the tests
./scripts/verify.sh     # formatting, linting, strict typing, tests, coverage
uv run python scripts/smoke.py   # end-to-end acceptance over a pseudo-terminal
```

## Documentation

The documents under [`docs/`](docs/README.md) are the specification for this codebase, and
[`docs/README.md`](docs/README.md) indexes them: architecture, the state machine, the runtime loop,
sessions and context, the TUI, tools and sandboxing, and configuration.

This repository is a Python port of a Go implementation of the same design, kept readable side by
side: exported identifiers keep their Go spelling, module names mirror the Go file names, and
[`tests/MIGRATION_MAP.md`](tests/MIGRATION_MAP.md) records which Go test each Python test covers.
`tests/fixtures/go_sessions/` holds a session store written by the Go implementation, so the
on-disk format is verified against real output rather than a description of it.

## Status

The Rich TUI is the only interaction surface; headless, server, and alternate UI entry points
are out of scope.
