# TUI

This document specifies the framework-neutral UI model and the retained legacy Textual adapter.
The `super-agent` command does not start that adapter. Its native, line-oriented interaction is
specified in [terminal.md](terminal.md).

`tui` is the inbound adapter and the only interaction surface. It depends on its `Conversation` port
and its own display DTOs, never on `runtime` — see `architecture.md`. Runtime values become TUI values
at the composition boundary in `app/tui_adapter.py`.

## Feature Architecture

The TUI is feature-oriented and has one-way dependencies. A stateful user capability is a feature;
stateless formatting helpers and visual primitives are not.

Each feature owns its state, update logic, effects, and view. Feature state is private: one feature
must not read or mutate another feature's model. Features collaborate only through explicit typed
messages or intents. They must not import sibling features or share mutable state.

The root `App` owns only application lifecycle, global message routing, focus, terminal dimensions,
and layout composition. It may contain feature models, route messages to them, and compose their
rendered output. It must not contain feature-specific state, key semantics, or business branches.
Only genuinely cross-feature state belongs at the root; convenience is not a reason to promote state.

Input belongs to the feature that currently owns focus. The root handles only truly global input,
such as application exit or terminal resize, and otherwise forwards input to the focused feature.
Meanings such as submitting a prompt, choosing an approval, or navigating a menu belong to their
respective features.

Views are pure renderers. A view may read only its feature model; it must not call a port or service,
mutate state, start work, or emit business events. I/O and other side effects run as commands returned
by the update/effect layer, and their typed result messages drive later updates.

Each feature defines the narrow ports required by its own use cases. TUI features must not depend on
runtime types, global services, or a shared interface that aggregates unrelated capabilities. The
composition boundary in `app` implements feature ports and converts runtime values to feature-owned
display DTOs. New capabilities extend the owning feature port instead of widening a common service
interface.

A TUI feature change should remain within that feature and its direct messages, ports, and adapters.
A change that requires knowledge of unrelated feature internals indicates a failed boundary and must
first be resolved by changing ownership or dependencies. Legitimate cross-feature flows may change
the participating features and their explicit contract, but must not create direct feature coupling.
The goal is controlled change propagation: changing one feature does not require synchronized edits
to unrelated features.

### Feature ownership

| Feature | Owns |
|---|---|
| `tui/composer` | The prompt input, its history, queued follow-ups, and the slash-command palette. Emits `SUBMIT`, `QUEUE`, `STEER`, and `CLEAR` intents. |
| `tui/transcript` | Committed messages, live streaming content, tool-call and reasoning expansion, and copying the latest code block. |
| `tui/approval` | The pending tool-approval request, its selection, and the decision the runtime receives. |
| `tui/attachments` | Files queued for the next turn. |
| `tui/commands` | The slash-command catalogue, each command's input semantics, and the compact and MCP operations they start. |

The root `App` owns the turn lifecycle, the cross-feature error and status line, the help overlay,
the welcome block, terminal dimensions, and layout composition. A feature request that reaches
another feature — a prompt, an attachment, a snapshot refresh — travels as an explicit field of the
requesting feature's outcome, and the root performs the wiring.

`tui` may not import `runtime`, and a feature may not import a sibling feature, the root package, or
the composition root `app`. `app` depends on `tui`, never the other way round. All of these rules are
enforced by `tests/architecture/test_dependencies.py`.

## Commands

A command reports its whole effect at once: the error line, the status line, and any output block. A
command that succeeds clears a previous error, and one that fails leaves the status line untouched,
because the error line takes precedence over it. Command output opens in a TUI-owned viewer; it is
never printed behind the alternate screen.

Session and configuration:

- `/instructions`: show the loaded instruction source paths.
- `/permissions`: show the current permission mode and tool approval status.
- `/permissions mode <ask|accept-edits|plan|bypass>`: change the session permission mode.
- `/sessions`, `/resume <id>`, `/rename <id> <title>`, `/delete-session <id>`, `/fork [title]`.

Agents:

- `/agent`: list built-in and configured agent profiles.
- `/agent <name>`: switch model, system instructions, and permission mode.
- `/plan`, `/build`, and `/mode <plan|build>`: shortcuts for the built-in profiles.
- Custom profiles live under `agents` in settings and may restrict tools.

MCP:

- `/mcp list`, `/mcp add <name> <command> [args...]`, `/mcp remove <name>`, `/mcp restart <name>`.

Context:

- `/memory`, `/remember <text>`, `/forget`: inspect, add, or clear cross-session memory.
- `/compact [summary]`: summarize and shrink the model context. See `session.md`.
- `/undo`: restore the latest non-empty checkpoint and truncate the transcript to match.
- `/attach <path>`, `/attachments`: queue a bounded workspace attachment for the next turn.

Workflows:

- `/review`, `/diff`, `/fix-ci`, `/branch`, `/commit-message`, `/diagnostics <path>`.
- `/export <markdown|json>` and `/share` write local files under `.super-agent/exports/`.

Extensions:

- `/commands`, `/skills`, `/plugins`: inspect discovered custom commands, `SKILL.md` instructions, and
  local plugin bundles.

## Keys

| Key | Behaviour |
|---|---|
| `Enter` | Submit when idle; cancel and restart with steering input while a turn runs |
| `Tab` | Queue a follow-up while a turn runs |
| `Ctrl+J`, `Shift+Enter`, `Alt+Enter` | Insert a newline in the composer |
| `/` | Open the command palette; arrows select, `Tab` or `Enter` completes |
| `Esc` | Clear input, or cancel a run |
| `Ctrl+U` | Clear input |
| `Ctrl+L` | Clear transient status and return the transcript to its latest content |
| `Ctrl+C` | Copy the selected text; otherwise clear a non-empty draft, cancel a run, or press twice to quit while idle |
| Arrows | Navigate multiline input, or recall a single-line prompt without losing the draft |
| `PgUp`, `PgDn` | Move the transcript viewport by one page |
| `Home`, `End` | Move the transcript viewport to its start or end when it owns focus |
| `Ctrl+Y` | Copy the latest assistant code block |
| `Ctrl+O` | Expand or collapse the latest tool-call group |
| `Alt+O` | Expand or collapse all tool-call groups |
| `Ctrl+R` | Expand or collapse the latest reasoning block |
| `Alt+R` | Expand or collapse all reasoning blocks |
| `Ctrl+T` | Open the transcript pager: `/` searches, `n` and `N` step through matches and wrap, and `Esc` closes the search prompt before the pager |
| `Ctrl+G` | Edit the composer draft in `$VISUAL` or `$EDITOR`, whichever is set first; the draft is left untouched when neither is set |
| `?` | Open help while the composer is empty; otherwise insert the character |
| `F1` | Open help |

### Key contexts

Keys belong to a named context, and exactly one context owns input at a time. A context is entered
by taking its focus and left by returning focus to the composer, so a shortcut is never ambiguous
between two visible surfaces.

| Context | Owner | Answers |
|---|---|---|
| `global` | The root app | Quit, help, terminal resize, and focus recovery |
| `chat` | `tui/transcript` | Viewport navigation and block expansion |
| `composer` | `tui/composer` | Editing, submission, history, and the command palette |
| `editor` | The external editor | Everything, while `$VISUAL` or `$EDITOR` owns the terminal |
| `pager` | The transcript pager | Scrolling and searching the transcript |
| `list` | `tui/commands` | Navigating a command or choice list |
| `approval` | `tui/approval` | Answering a pending tool approval |

This is the focus rule above, named: input belongs to the feature that owns focus. The root keeps
only the genuinely global keys.

## Mouse

The application requests mouse reporting, so the wheel and the pointer reach the interface.

- The wheel scrolls the surface under the pointer. A surface with nothing to scroll — the composer,
  the status line — leaves the wheel to the transcript, so the conversation scrolls wherever the
  pointer happens to be. An open overlay keeps the wheel to itself.
- The transcript draws no scrollbar of its own: the wheel, `PgUp`, and `PgDn` are how it moves, and
  the unread indicator at its foot reports when it is pinned above the latest content. An overlay
  that shows long output — help, a command's output, the pager — keeps a scrollbar, because nothing
  else there says where it is.
- Dragging inside the transcript selects text and leaves focus in the composer, so the next keypress
  still edits the draft.
- The terminal keeps its own selection behind its bypass modifier: hold `Shift` while dragging in
  xterm, kitty, and GNOME Terminal to select and copy text exactly as it is drawn.

Composer rules worth knowing:

- The composer is multiline. `Enter` submits; the newline bindings above do not.
- Prompt-history navigation preserves and restores the current unsubmitted draft.
- Typing `/` opens the palette. The full palette shows descriptions and argument hints; compact mode
  shows names only.

Run rules:

- Queued prompts run in order. The composer area previews the first three and summarises the
  remainder.
- Manual cancellation with `Esc` or `Ctrl+C` clears queued prompts. Steering cancellation preserves
  them, because the user is mid-thought rather than abandoning the work.

## Layout

The TUI uses the terminal's alternate screen and restores the previous terminal contents and modes on
normal exit, cancellation, and failure. The transcript is a retained scrollable viewport; the
composer and status line remain fixed below it. Approval, help, command choices, and long command
output use overlays and restore focus to its previous owner when closed.

- The compact bordered welcome card contains the product name and version, model, working directory,
  and final loaded instruction-source filename. A short `/help` tip sits directly below it.
- The composer is a rounded bordered prompt with `Ask Super Agent to do anything` as its empty-state
  hint. The status line remains directly below it.
- User prompts are visually prominent. Assistant prose wraps to the available width without an extra
  left indent. Code preserves indentation and remains accessible horizontally; content is never
  silently truncated.
- Reasoning defaults to a compact `Thinking...` line. The reasoning text expands in place for the
  latest or all model steps with the keys above.
- Tool calls are compact action summaries. Expanding or collapsing one block updates that block in
  place and keeps the viewport stable.
- New output follows the bottom only while the viewport is already at the bottom. Scrolling upward
  pins the viewport; later output increments an unread indicator until the user returns to the end.
- Streaming mutates only the active assistant block and never resets the scroll offset.
- Reset, resume, compact, and undo reconcile the retained transcript from current conversation state.
- Resize preserves the draft, selection, transcript position, pending approval, and active stream.
- Below 18 terminal rows, queue details and command choices use their compact forms.
- The status line is an ordered row of items selected by `tui.status_line` in settings — see
  `config.md`. An item whose data is unavailable is omitted rather than shown empty, and setting the
  key to `null` removes the row and returns its height to the transcript.

## Appearance

Colour carries meaning and is never decoration. The TUI draws from the terminal's own ANSI palette
and its default foreground and background, so the interface follows whatever colour scheme the
terminal is configured with — dark or light — without a theme to pick.

| Role | Colour |
|---|---|
| Default text, assistant prose, tool output | The terminal's default foreground |
| Secondary text: reasoning, metadata, hints, tree guides | Default foreground, dimmed |
| User input, selection, status indicators | ANSI cyan |
| Success and added lines | ANSI green |
| Errors, failures, and removed lines | ANSI red |
| The agent's identity marker | ANSI magenta |

Those six roles are the whole vocabulary. The TUI never uses ANSI blue or yellow as a foreground,
never uses ANSI black or white as a foreground, and never constructs a colour from an RGB triple, a
hexadecimal literal, or an indexed palette entry. `tests/architecture/test_theme.py` enforces this
by parsing the TUI's own colour construction sites.

Because colour is delegated to the terminal, a screenshot of this interface is not a colour
reference. Syntax highlighting inside fenced code is the one exception: it is selected by name from
`tui.syntax_theme` and is not defined by the TUI.

`NO_COLOR` removes colour entirely: every role is drawn in the terminal's default foreground and
background, so the interface stays legible on any colour scheme. A terminal that cannot show ANSI
colour is treated the same way. Degrading to monochrome is not the same as degrading to greyscale —
a role resolved to a grey is still a colour the terminal did not choose, and a role resolved to black
is invisible on a dark background.

Message markers, in the roles above:

- A user prompt is marked `❯` in cyan.
- The agent's reply is marked `●` in magenta.
- A tool call is a compact action summary in cyan; expanding it reveals detail beneath a tree guide
  in dimmed default text.
- Reasoning is a single dimmed line until expanded.

## Approval UI

Tool approval is a modal selectable menu rather than a bare prompt:

- The prompt owns the keyboard while it is open: no key reaches the composer or the transcript, so a
  shortcut or an answer is never typed into the draft behind it.
- Arrows or `j`/`k` move the selection; `Enter` confirms.
- `1`/`y`, `2`/`a`, and `3`/`n` remain direct shortcuts for approve-once, always-approve, and deny.
- A submitted decision ignores repeated keys until the runtime advances, so a double keypress cannot
  answer the next prompt by accident.
- The prompt stays open until the runtime moves on, and closing it returns focus to the composer.

The engine reports live states while actions run, so the status line follows `WaitingApproval` and
`RunningTool` as they happen rather than only at snapshot boundaries.
