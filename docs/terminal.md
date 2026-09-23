# Terminal Interface

`super-agent` uses the terminal's normal input, output, and scrollback. It does not enter the
alternate screen or draw retained panes, borders, overlays, a fixed composer, or a transcript
viewport.

The `❯` prompt reads one message. Replies, tool activity, approvals, errors, and slash-command
results are appended to stdout. The terminal owns scrolling, selection, copying, and search.

An approval is an inline numbered prompt. Enter `1` to allow once, `2` to always allow, or `3` to
deny. EOF exits; `/quit` and `/exit` provide explicit exits.

The framework-neutral models and conversation port remain under `tui/`, but the console-script entry
point uses `TerminalApplication`. The old Textual application is retained only for compatibility and
is not an interaction surface started by `super-agent`.
