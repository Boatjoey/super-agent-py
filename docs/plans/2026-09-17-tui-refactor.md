# Real TUI Refactor Implementation Plan

**Goal:** Replace the Rich `Live` text redrawer with a real full-screen TUI that has a scrollable transcript, native multiline editing, stable streaming updates, overlays, and responsive layout.

**Architecture:** Keep `tui` as the only inbound adapter and preserve the existing `Conversation` ports and display DTOs. Use Textual only inside `tui`; the composition boundary remains `app/tui_adapter.py`. Each feature owns its widgets, messages, and local presentation state. The root application owns lifecycle, focus, global bindings, and feature wiring.

**Tech Stack:** Python 3.12, Textual, Rich renderables inside Textual widgets, pytest, Textual pilot tests.

---

## Target behaviour

The replacement is complete only when all of these are true:

- The program uses the terminal alternate screen and restores it on normal exit, cancellation, and failure.
- The transcript is a real viewport. Mouse wheel, `PgUp`, `PgDn`, `Home`, and `End` navigate retained content.
- Long prose and code wrap or scroll according to their content type; no visible content is silently truncated.
- New output follows the bottom only while the user is already at the bottom. Scrolling upward pins the viewport and shows an unread-output indicator.
- Streaming updates mutate only the active assistant block. They do not rebuild the full transcript or reset its scroll offset.
- The composer supports multiline selection, cursor movement, deletion, paste, and terminal input methods through a real text-area widget.
- The transcript, composer, approval dialog, command palette, help, queue, and status bar remain independently testable features.
- Resize changes layout without losing the draft, selection, transcript position, pending approval, or active stream.
- Existing session, permission, MCP, attachment, agent, memory, workflow, cancellation, queue, and steering behaviour is preserved.
- `tui` still does not import `runtime`; runtime values are converted in `app/tui_adapter.py`.

## Non-goals

- Do not change the runtime machine, engine scheduling, session semantics, or persistence format.
- Do not add a headless or second UI entry point.
- Do not keep the old renderer behind a permanent flag.
- Do not reproduce a web UI, terminal emulator, or editor-grade syntax engine.

## Proposed package shape

```text
super_agent/tui/
├── application.py          # Textual App: lifecycle, global bindings, wiring
├── theme.tcss              # layout and visual tokens
├── conversation.py         # existing ports and display DTOs
├── transcript/
│   ├── model.py            # message presentation and expansion state
│   ├── screen.py           # scroll viewport and auto-follow policy
│   └── widgets.py          # message, reasoning, tool, stream widgets
├── composer/
│   ├── model.py            # history, queue, and submit intent rules
│   └── widget.py           # multiline editor and command suggestions
├── approval/
│   ├── model.py
│   └── dialog.py
├── attachments/
├── commands/
│   └── palette.py
└── status/
    └── widget.py
```

`conversation.py` stays framework-neutral. Feature models keep behavioural rules that can be tested without a terminal. Widgets translate Textual events into feature intents; they never call session or runtime objects directly. `application.py` invokes the narrow ports, routes results, and converts feature intents into cross-feature effects.

---

### Task 1: Replace the TUI specification before implementation

**Files:** Modify `docs/tui.md`, `docs/architecture.md`, `README.md`, and `pyproject.toml`.

- Specify alternate-screen lifecycle, viewport navigation, wrapping, auto-follow, unread output, focus, overlays, mouse behaviour, and resize guarantees.
- Remove the claim that terminal scrollback owns transcript navigation.
- Record Textual as an adapter dependency, not a domain dependency.
- Keep each normative behaviour in `docs/tui.md`; link to it elsewhere instead of duplicating it.
- Add Textual to project dependencies and refresh `uv.lock`.

**Acceptance:** Documentation describes observable behaviour rather than widget implementation, and dependency tests still encode the documented boundary.

### Task 2: Add the Textual application shell

**Files:** Create `super_agent/tui/application.py` and `super_agent/tui/theme.tcss`; modify `super_agent/__main__.py`, `super_agent/tui/__init__.py`, and `tests/tui/test_application.py`.

- Create one full-screen application with transcript, footer, composer, status bar, and overlay layers.
- Map only global bindings at the root: quit/cancel, help, focus recovery, and screen-wide actions.
- Start and stop conversation listeners with the application lifecycle.
- Ensure background tasks are cancelled and awaited during shutdown.
- Replace the `Program` construction in `__main__.py`; do not expose a selectable legacy path.

**Acceptance:** A pilot test starts and exits the application, verifies focus begins in the composer, resizes the terminal, and proves cleanup leaves no pending task.

### Task 3: Build a retained, scrollable transcript

**Files:** Create `super_agent/tui/transcript/screen.py` and `super_agent/tui/transcript/widgets.py`; modify `super_agent/tui/transcript/model.py` and transcript tests.

- Represent each committed message as a stable keyed widget instead of regenerating one large `Text` value.
- Render user, assistant, reasoning, tool call, tool result, interruption, and attachment blocks separately.
- Wrap prose to viewport width. Preserve code indentation and provide horizontal handling without truncation.
- Implement viewport bindings and mouse-wheel navigation.
- Track whether the viewport is at the bottom. Auto-follow only in that state; otherwise retain the offset and increment an unread marker.
- Keep reasoning and tool expansion local to their owning block so toggling one block does not rebuild unrelated history.
- Load resumed and reset conversations through one keyed reconciliation path.

**Acceptance:** Tests cover long wrapped text, content taller than the terminal, upward scrolling during streaming, return-to-bottom, resize, expansion without scroll jumps, and transcript replacement after resume/reset/undo/compact.

### Task 4: Replace the handwritten composer with a real editor widget

**Files:** Create `super_agent/tui/composer/widget.py`; modify `super_agent/tui/composer/model.py` and composer tests.

- Delegate cursor movement, multiline editing, selection, Unicode input, and paste to the text-area widget.
- Keep prompt history, queued follow-ups, submit/queue/steer rules, and slash matching in the feature model.
- Display command suggestions as a positioned overlay without consuming transcript height.
- Preserve the current draft when opening help, approval, or command overlays and across terminal resize.
- Define explicit focus return after every overlay closes.

**Acceptance:** Pilot tests cover multiline editing, selection replacement, paste, history draft restoration, slash completion, queue, steering, and overlay focus restoration.

### Task 5: Implement modal approval, help, and command surfaces

**Files:** Create `super_agent/tui/approval/dialog.py` and `super_agent/tui/commands/palette.py`; add the help overlay; modify their feature tests.

- Show approval as a modal with a single-decision latch and explicit selected action.
- Keep direct approval shortcuts while preventing repeated keys from answering a later request.
- Show help and the full command palette as overlays with keyboard navigation and escape-to-close.
- Trap focus inside a modal and restore it to the prior owner on close.
- Route command output into a TUI-owned output viewer or transcript system block; do not print behind the alternate screen.

**Acceptance:** Tests prove modal focus, decision idempotence, command selection, error display, and no writes to hidden terminal scrollback.

### Task 6: Reconnect conversation notifications and background work

**Files:** Modify `super_agent/app/tui_adapter.py`; add `super_agent/tui/controller.py`; modify application and adapter tests.

- Keep `Channel`, `Cancellation`, `TurnPort`, and display DTOs independent of Textual.
- Give each turn an identity and discard stale notifications from replaced turns.
- Convert notification consumption, turn completion, attachment loading, compacting, and MCP operations into supervised application workers.
- Marshal every widget mutation onto the UI event loop.
- Preserve manual cancellation, steering cancellation, approval waiting, queued prompts, and error reporting.

**Acceptance:** Integration tests use the real session adapter to cover streaming, tool approval, cancellation, stale events, queued turns, and a failing background operation.

### Task 7: Enforce boundaries and delete the legacy renderer

**Files:** Delete `super_agent/tui/runtime.py` and `super_agent/tui/view.py`; simplify `super_agent/tui/update.py` and `super_agent/tui/app.py` or replace them with narrowly owned modules; modify `tests/architecture/test_dependencies.py` and remove obsolete tests.

- Remove `Rich Live`, raw `termios` input decoding, manual escape-sequence parsing, tail clipping, line truncation, global active-console state, and scrollback printing.
- Add architecture checks preventing feature widgets from importing sibling features, `runtime`, or `app`.
- Keep Rich only for renderable content and styling where Textual accepts it.
- Remove compatibility exports once all internal callers use the new surface.

**Acceptance:** Searches find no `Live`, `KeyDecoder`, `fitDynamicArea`, `clampLines`, `_active_console`, or legacy `Program` in production TUI code.

### Task 8: Validate terminal behaviour and release readiness

**Files:** Add focused tests under `tests/tui`; update `docs/contributing.md` with the manual terminal matrix.

- Run automated tests at narrow, standard, and wide terminal sizes.
- Exercise rapid streaming, thousands of messages, repeated resize, cancellation, and shutdown under asyncio debug mode.
- Manually verify a minimal matrix: Linux terminals, tmux, SSH, bracketed paste, Unicode/wide glyphs, mouse wheel, and terminals without clipboard helpers.
- Measure transcript append and stream-update latency with a large retained history; fix full-history re-rendering before release.
- Run `uv run ruff format`, focused tests, `PYTHONASYNCIODEBUG=1 uv run pytest tests/tui`, and `./scripts/verify.sh`.

**Acceptance:** Automated checks pass, the manual matrix is recorded, and no known path loses content, scroll position, draft text, approval state, or terminal state.

## Migration order

Implement Tasks 1–6 while the legacy modules remain available only as internal migration scaffolding. Switch `__main__.py` after the new shell, transcript, composer, overlays, and notification bridge pass integration tests. Then complete Task 7 in the same change set: delete the legacy implementation rather than shipping two interaction surfaces.

## Main risks

- **Framework leakage:** contain Textual types inside `tui`; keep ports and DTOs framework-neutral.
- **Scroll jumps:** key widgets by message identity and update the streaming block in place.
- **Async races:** supervise workers, tag turn notifications, and test cancellation at every await boundary.
- **Behaviour regression:** retain pure feature-model tests and add pilot tests for user-visible interaction.
- **Large histories:** avoid rebuilding every message on each stream chunk; reconcile only changed blocks.
- **Terminal recovery:** cover exceptions and forced cancellation so the alternate screen and input modes are always restored.

## Definition of done

The refactor is done when the Textual application is the sole TUI, all target behaviours are covered, legacy renderer code is deleted, architecture tests pass, `./scripts/verify.sh` succeeds, and manual terminal results are recorded in the pull request.
