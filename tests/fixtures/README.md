# Checked-in golden session store

`session_store/` is a checked-in golden session store, kept so the store can be
tested against a fixed on-disk layout rather than only against artefacts it just
produced itself.

It contains one record of each of the ten types, so every replay path is
represented:

| Record | Present |
|---|---|
| `session_started` | yes (once from `Create`, once explicit) |
| `message_appended` | four, including a `tool_calls` message |
| `tool_result` | yes |
| `approval_decision` | yes |
| `checkpoint` | yes, with a file entry |
| `error` | yes |
| `cancel` | yes |
| `compact` | yes, with `original_messages` and `kept_messages` |
| `reset` | yes |
| `context_replaced` | yes |

`_memory.json` sits at the store root. A second session directory exists so the
`List` ordering and "not every session is loaded" paths are exercised.

The timestamps use RFC 3339 with nanosecond precision and trailing-zero
trimming: `12:00:01` has no fractional part at all, while `12:00:00.123456789`
keeps all nine digits. Directory modes are `0700` and file modes `0600`.

Regenerate by writing the ten record types through `store.Store.Append`; nothing
here is hand-written.
