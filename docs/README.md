# Documentation

**These documents are the specification.** Behaviour changes start here, not in the code. If the code
and a document disagree, the code is wrong until the document is deliberately changed.

The workflow that follows from that rule:

1. Change the relevant document first.
2. Implement the change.
3. Ship both in the same change.

A behaviour change that `docs/` does not reflect is incomplete, even when the code works.

## One Home Per Fact

Every normative fact has exactly one authoritative home. Other documents link to it and never restate
it. Five copies of the transition table is five things that can drift — and this documentation set
previously had five, which is why it was consolidated.

When you are tempted to restate something, link to it instead.

## Contents

| Document | Covers |
|---|---|
| [architecture.md](architecture.md) | Hexagonal layout, the dependency rule, package and file responsibilities |
| [machine.md](machine.md) | States, the canonical transition graph and table, runtime data, invariants, errors |
| [runtime.md](runtime.md) | The runtime cycle, the engine loop, scheduled actions, run lifecycle, telemetry |
| [session.md](session.md) | Context assembly, instructions, persistence, resume, compact, reset, undo |
| [tui.md](tui.md) | Feature architecture, commands, keys, approval UI, layout rules |
| [tools.md](tools.md) | Registry, built-in tools, MCP, LSP, network guards, the sandbox |
| [config.md](config.md) | `settings.json`, providers, permissions, sandbox parameters, flags |
| [workspace.md](workspace.md) | The workspace model — a survey of coding-agent workspace concepts plus super-agent's project resolution, access policy, persistence, and resume |
| [contributing.md](contributing.md) | The doc-first workflow, tests, git conventions, build |

## Reading Order

New to the project, or tracing one turn end to end:

```text
architecture.md   where the pieces are and which way dependencies point
  -> machine.md    what the states are and how they connect
  -> runtime.md    how a transition is applied and how the loop is driven
  -> session.md    what the agent remembers and how it is persisted
```

For a specific change, go straight to the owning document: state-machine changes to `machine.md`,
adapter changes to `architecture.md`, and so on.

## Enforcement

`tests/architecture/test_spec.py` parses `machine.md` and verifies it against the running machine. It
enumerates every state against every declared event and fails when the set of accepted transitions, or
their destinations, differs from the documented table — in either direction. It also checks that the
state diagram in the same file agrees with the table.

`tests/architecture/test_dependencies.py` enforces the dependency rule the same way, by parsing imports.

Treat a failure in either test as a documentation defect first. Amending the document is the fix; the
code follows.
