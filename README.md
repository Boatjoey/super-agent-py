# Super Agent

Python agent runtime with a state-machine core, LLM providers, local tools, and a native terminal
interface.

## Quick Start

```bash
uv run super-agent
```

The terminal interface writes normal output into scrollback; it has no alternate screen or panes.
On first run the app writes a settings template to
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
sessions and context, the terminal interface, tools and sandboxing, and configuration.

`tests/fixtures/session_store/` holds a checked-in golden session store, so the on-disk format is
verified against a fixed layout rather than a description of it.

## Status

The native terminal is the only interaction surface; headless, server, and alternate UI entry
points are out of scope.
