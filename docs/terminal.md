# Interactive CLI

`super-agent` exposes one interaction surface: `TerminalApplication`, an interactive CLI. It uses
normal terminal input, output, and scrollback. It never enters the alternate screen or draws retained
panes, overlays, a fixed composer, or a transcript viewport.

The `❯` prompt reads one message. Replies, tool activity, errors, status changes, and slash-command
results append to stdout. The terminal owns scrolling, selection, copying, and search. Rich supplies
Markdown rendering and ANSI styling but does not own the terminal lifecycle.
Input uses UTF-8. An invalid input byte is replaced with `�` so it cannot terminate the CLI.

Assistant text appears incrementally as model chunks arrive. The committed message completes the
same reply without printing its streamed text twice. Reasoning, when supplied, uses the secondary
style. Tool results and errors remain separate scrollback entries. Streaming and approval notices
must remain visible without waiting for another prompt.

On an interactive terminal, typing `/` opens a command list above the current prompt. Further
characters filter it by command-name prefix; Up and Down change the selection, Tab completes it,
and Enter completes a selected partial name before a second Enter runs it. The list disappears when
the draft stops being a command prefix. Piped input is line-oriented and runs complete slash commands
without a live list. Neither mode enters the alternate screen.

An approval is an inline numbered prompt. Enter `1` to allow once, `2` to always allow, or `3` to
deny. `y`, `a`, and `n` are equivalent. EOF exits; `Ctrl+C` cancels an active turn and exits when idle.
`/quit` and `/exit` provide explicit exits.

The framework-neutral interaction model and conversation port live under `tui/` for compatibility;
the package name does not describe a second product surface. No Textual or full-screen adapter exists.

## Commands

Slash commands use the same command model as the interactive loop:

- Session: `/clear`, `/compact`, `/sessions`, `/resume`, `/rename`, `/delete-session`, `/fork`, `/undo`.
- Permissions and agents: `/permissions`, `/agent`, `/plan`, `/build`, `/mode`.
- Context: `/attach`, `/attachments`, `/memory`, `/remember`, `/forget`, `/instructions`.
- Workflows: `/review`, `/diff`, `/fix-ci`, `/branch`, `/commit-message`, `/diagnostics`, `/export`, `/share`.
- Extensions: `/mcp`, `/commands`, `/skills`, `/plugins`.

Command output is appended to scrollback. A successful command clears an earlier error; a failed
command leaves the prior status untouched because the error takes precedence.

## Appearance

The interface uses the terminal's default foreground and ANSI cyan, green, red, and magenta. It does
not construct RGB, hexadecimal, or indexed colours. `NO_COLOR` disables colour. Syntax highlighting
inside fenced code is the only independently selected theme.
