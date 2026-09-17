# Configuration

## Sources

| Source | Provides |
|---|---|
| `.env` (via `python-dotenv`) | Runtime switches such as `NO_TOOLS` and `YOLO` |
| `~/.superagent/settings.json` | Providers, permissions, sandbox, servers, agents, extensions, telemetry |
| Command-line flags | `--yolo`, `--no-tools`, `--approval-mode` |
| `~/.superagent/AGENTS.md` and project `AGENTS.md` | Layered instructions — see `session.md` |

If `settings.json` is missing, the app creates a template on startup. Invalid values — an unknown
permission mode, for instance — fail config load rather than falling back silently.

## Providers

```json
{
  "provider": "deepseek",
  "providers": {
    "deepseek": { "base_url": "https://api.deepseek.com", "api_key": "sk-...", "model": "deepseek-reasoner" },
    "openai":   { "api_key": "sk-...", "model": "gpt-4o" },
    "claude":   { "api_key": "sk-ant-...", "model": "claude-3-7-sonnet-20250219" }
  }
}
```

`provider` selects the default. Adapters live in `llm/`; the OpenAI-compatible providers send the
system prompt as a chat `system` message, Claude sends it through the Anthropic `system` field.

The selected provider's credential is resolved at config load. The template placeholder (`sk-...`,
`sk-ant-...`) counts as unset, and `<PROVIDER>_API_KEY` — `DEEPSEEK_API_KEY`, for example — supplies
the credential when the settings entry leaves it unset. A provider that is absent from `providers`,
or that ends up with no credential, fails config load with a message naming what is missing rather
than failing later as an authentication error on the first turn.

**The resolved configuration is the one the model is built from.** `new_session_with_extensions`
constructs the adapter from the resolved entry, never from the raw `providers` map, so a settings
file that still holds the template placeholder cannot send `sk-...` as a bearer token. A custom agent
profile that names another provider uses that provider's entry as written, and a profile that
overrides `model` layers that override on top of the entry it selected.

## Agents

The top-level `agent` selects `build`, `plan`, or a profile from `agents`. Custom profiles may override
`provider`, `model`, `prompt`, `tools`, and `permission_mode`:

```json
{
  "agent": "build",
  "agents": {
    "reviewer": {
      "prompt": "Review changes and report defects.",
      "permission_mode": "plan",
      "tools": ["read_file", "search", "git_diff", "lsp_diagnostics"]
    }
  }
}
```

Switching profiles clears the current transcript.

## Permissions

```json
{
  "permissions": {
    "mode": "ask",
    "network": "deny",
    "allow_tools": [],
    "deny_tools": [],
    "allow_command_prefixes": [],
    "deny_command_prefixes": [],
    "allow_paths": [],
    "deny_paths": [],
    "allow_env": [],
    "deny_env": []
  }
}
```

Modes are `ask`, `accept-edits`, `plan`, and `bypass`; `--yolo` maps to `bypass`.

**`YOLO=true` in `.env` enables bypass only when no explicit `--approval-mode` flag was passed.** The
flag always wins over the environment, so a checked-in `.env` cannot silently disable permission
prompts.

Command classification routes approvals and is not a security boundary; the sandbox described in
`tools.md` is what contains execution.

## Sandbox

```json
{
  "sandbox": {
    "mode": "strict",
    "cpu_seconds": 120,
    "memory_mb": 1024,
    "max_processes": 128,
    "max_open_files": 256
  }
}
```

`strict` is the default and fails closed when `bwrap` or `prlimit` is unavailable. Unsupported
platforms require an explicit `sandbox.mode: off`. Behaviour is described in `tools.md`.

## Servers

`mcp_servers` declares MCP stdio servers by name; `lsp_servers` declares language servers by extension.
Both are described in `tools.md`.

## Extensions

```json
{
  "extensions": {
    "commands": { "explain": "Explain $ARGUMENTS" },
    "hooks": { "after_turn": ["uv run pytest -q"] },
    "skills": [".superagent/skills/reviewer"],
    "plugins": [".superagent/plugins/team"]
  }
}
```

Extensions provide prompt-backed slash commands, lifecycle hooks, `SKILL.md` instructions, and local
plugin manifests. Tool hooks must use recursion-safe direct execution so a hook cannot re-enter the
observer that invoked it. Hook events cover session start, before and after turns, pre and post tool
calls, approvals, and errors.

Commands, skills, and plugins are also discovered under user and project `.superagent/` directories.

## Telemetry

```json
{ "telemetry": { "log_path": "/absolute/or/workspace/relative.jsonl" } }
```

Defaults to `~/.superagent/telemetry.jsonl`. Records and their fields are described in `runtime.md`.

## TUI

```json
{
  "tui": {
    "status_line": ["model", "approval", "context_usage"],
    "syntax_theme": "ansi_dark"
  }
}
```

`status_line` is an ordered list, drawn left to right. The available items are `model`, `approval`,
`context_usage`, `session_id`, `sandbox`, `cwd`, and `spinner`. An item whose data is unavailable is
omitted rather than shown empty, and an unknown item fails config load. Setting the key to `null`
removes the row entirely and returns its terminal height to the transcript. The default is
`["model", "approval", "context_usage"]`.

`context_usage` shows the token counts the provider reported for the most recent model call. The
runtime carries no context-window size, so the item shows absolute counts rather than a percentage,
and it is omitted until a provider reports usage.

`syntax_theme` names the theme used to highlight fenced code blocks. `ansi_dark` and `ansi_light`
draw from the terminal's own palette, like the rest of the interface; the other named themes carry
their own colours. It affects code blocks only — the interface colours are specified in
`tui.md#appearance` and are not configurable.

## Flags and Environment

`--cwd <directory>` explicitly selects the project directory. Relative values are resolved from the
process cwd. Without it, project resolution walks upward from the process cwd looking for `.git` and
falls back to that cwd. The resolved project root becomes the initial workspace primary root and cwd.

| Switch | Effect |
|---|---|
| `--cwd <directory>` | Select the project directory explicitly |
| `--yolo` | Auto-approve tools; maps to `bypass` |
| `--no-tools` | Disable tool calling |
| `--approval-mode <ask\|accept-edits\|plan\|bypass>` | Set the permission mode |
| `NO_TOOLS=true` | Disable tools from the environment |
| `YOLO=true` | Enable bypass when no explicit flag is passed |
