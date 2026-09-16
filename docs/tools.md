# Tools

Tools are outbound adapters. They may import `runtime/protocol` but not the root `runtime` facade —
see `architecture.md`.

## Registry

`tools.Registry` holds the visible tools. Both `tools.DefaultRegistry` and `tools.SandboxedRegistry`
require the application to inject one workspace context when constructing the built-ins; tools do not
discover their policy from the process working directory.
`Registry.Add` merges dynamically discovered tools
atomically and rejects a batch outright when a name is empty, duplicated within the batch, or already
registered, so a partial merge cannot leave the set inconsistent. `tools.DefaultRegistry` builds the
built-ins; `tools.SandboxedRegistry` wires the same set to a sandboxed command runner.

## Built-in Tools

Thirteen tools ship in `DefaultRegistry`:

| Tool | Purpose |
|---|---|
| `read_file` | Read workspace files, with optional line ranges |
| `list_files` | List workspace files, with optional glob filtering |
| `search` | Search workspace files by regular expression |
| `apply_patch` | Replace expected text in a workspace file |
| `write_file` | Write workspace files, creating parent directories |
| `run_command` | Run workspace commands with cwd, timeout, and output limits |
| `go_test` | Run `go test` for workspace packages |
| `format` | Run `gofmt -w` on workspace files |
| `git_status` | Show `git status --short` |
| `git_diff` | Show `git diff` for optional paths |
| `bash` | Run shell commands after approval |
| `web_search` | Search the public web after network approval |
| `browser_fetch` | Fetch public HTTP(S) pages with redirect, size, timeout, and private-address protections |

`delegate` is registered by `app` (`app/session.go`) rather than by the tools package. It runs a task
in a child agent and returns the final result; see `session.md` for child sessions and worktrees.

Every built-in file-oriented tool resolves paths and checks read or write access through the injected
workspace context. Relative paths resolve from the workspace cwd. Containment uses canonical paths
and path-component-aware relative checks, including the nearest existing ancestor for paths that will
be created; lexical prefixes and symlinks cannot grant access. LSP file reads use the same policy, and
language-server processes start in the workspace cwd. Strict command sandbox construction takes its
workspace bind root from the same injected context rather than the process cwd.

File tools open a resolved path with `O_NOFOLLOW`, so a symlink swapped in for its final path
component between the containment check and the open cannot redirect the read or write outside the
workspace. Swapping a deeper path component remains possible and is accepted as residual risk.

`read_file` and `apply_patch` refuse a file larger than 10 MiB rather than loading it into memory,
and `search` bounds each line to 1 MiB so one minified or machine-generated line cannot abort the
scan. `list_files` and `search` skip entries they cannot resolve or read — including symlinks that
point outside the workspace — instead of failing the entire listing or search.

The injected workspace is a switchable runtime binding. A successful session resume replaces its
validated context, and subsequent built-in filesystem and command calls observe the restored cwd and
roots. Language-server clients lazily reconnect when that cwd changes. MCP servers are configured
external processes and are not a filesystem-policy adapter; their lifecycle is unchanged by workspace
resume.
Risky tools require policy approval unless the active mode allows them.

Command tools default to the workspace cwd. A model-supplied command cwd must resolve to a readable
workspace directory. Workspace checks are not an operating-system security boundary: once a shell
process starts, the context alone cannot prevent commands such as `cat ~/.ssh/id_rsa`. The independent
OS sandbox described below is responsible for containing subprocess filesystem access when enabled.

## MCP

`tools/mcp` speaks MCP over stdio. Servers are declared in the top-level `mcp_servers` settings map,
keyed by name; each entry accepts `command`, `args`, `env`, `cwd`, `connect_timeout_seconds`, and
`call_timeout_seconds`.

- Discovered input schemas are mapped to `protocol.ToolSpec`.
- Calls have deadlines and bounded output.
- Discovered tools join `tools.Registry` atomically and are **always risky** under the common
  permission policy, regardless of what the server claims about itself.
- Server environment variables are explicit, except for basic process variables such as `PATH` and
  `HOME`.

`app.MCPController` coordinates lifecycle, dynamic registry changes, rollback, and atomic settings
persistence. Add and remove update `settings.json` atomically. The runtime session owns extension
shutdown.

## LSP

`tools/lsp` starts stdio language servers declared in the `lsp_servers` map, matched by file extension.
Each entry takes `command`, `args`, `extensions`, and `language_id`. Configured servers expose
`lsp_diagnostics`, `lsp_symbols`, `lsp_definition`, `lsp_references`, and `lsp_outline`. Diagnostics are
push-based.

## Network Tools

`web_search` and `browser_fetch` are risky network tools and use the common permission flow. A browser
fetch accepts only public HTTP(S) targets, rejects local and private DNS results, caps redirects,
response size, and total time, and extracts page text without executing scripts.

## Sandbox

Permission modes route approvals; they are **not** a security boundary. Command classification is a
text heuristic and can be wrong, which is why command execution is contained separately.

On Linux, `tools/sandbox_linux.go` runs commands under strict bubblewrap isolation by default:

- the host root is mounted read-only;
- the workspace is the only writable host bind;
- temporary and home directories are ephemeral;
- networking follows the configured network policy (`permissions.network`);
- `prlimit` bounds CPU time, address space, process count, and open files.

Sandbox parameters come from the top-level `sandbox` settings; see `config.md`.

Strict mode **fails closed**: if `bwrap` or `prlimit` is unavailable, commands do not run. On
unsupported platforms the sandbox must be explicitly disabled with `sandbox.mode: off`, which then runs
commands with the current user's authority.
