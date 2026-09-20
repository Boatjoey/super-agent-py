# Checked-in golden session store

`session_store/` is a checked-in golden session store, produced outside this codebase. Replay runs
against this fixed on-disk layout rather than against a store the writer has just written, so a reader
change that quietly assumes the writer's own output fails here.

It holds two session directories and `_memory.json` at the root. The main session carries one record of
each of the ten record types, so every replay path is represented:

| Record | Present |
|---|---|
| `session_started` | two in the main session, one without a turn id and one carrying a turn id; one in the second session |
| `message_appended` | four: the `system` prompt, a `user` message, an assistant message with `reasoning_content`, and an assistant message with `tool_calls` |
| `tool_result` | one, pairing `call-1` (`bash`) with `/tmp/workspace` |
| `approval_decision` | one, `once` |
| `checkpoint` | one, holding a single file entry (`a.txt`, content `old`, mode `420`) |
| `error` | one, `something failed` |
| `cancel` | one |
| `compact` | one, with `summary`, `original_messages`, and `kept_messages` |
| `reset` | one |
| `context_replaced` | one, carrying the replacement transcript |

The second session directory, `20260916T130000000000000`, holds a single `session_started` record, so
`store.Store.list` sees more than one session and orders the two by `updated_at`, newest first.
`_memory.json` carries `remember this` and `and this`.

Timestamps are RFC 3339 with fractional seconds to nanosecond precision and trailing zeros trimmed:
`2026-09-16T12:00:00.123456789Z` keeps all nine digits, `2026-09-16T09:44:54.026498Z` keeps the six that
are not zeros, and `2026-09-16T12:00:01Z` omits the fraction entirely.

Mode bits are not part of the fixture: git preserves only the executable bit, so a checkout materialises
the files and directories under the local umask. Replay must not depend on a stored mode.

## Regenerating

Write one record of each type through `store.Store.append` into a fresh root, or edit the JSONL
directly, then restore the timestamp fractions and the second session directory above. A store written
end to end by the writer is not a substitute: the writer formats fractional seconds from microseconds,
so six digits is its limit and this layout is out of its reach. `tests/runtime/test_session_store.py`
replays every record type and lists both sessions; run it against any replacement.
