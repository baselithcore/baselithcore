# Audit Log

Tail-readable view of the JSON-Lines audit trail written for every
privileged action.

## What the page does

- **Stat cards** — quick counts by status.
- **Tail size selector** — 50/100/200/500/1000 most recent entries.
- **Action filter** — substring match on the action name (e.g. `shell_run`,
  `fs_write`, `mouse_click`).
- **Entry list** with a status badge (`success` → ok, `denied` → warn,
  `error` → err) and a primary-detail line (raw payload, argv, path, or cwd,
  whichever is present).
- **Detail drawer** for the full entry payload.
- Auto-refreshes on a 5s interval; manual refresh available.

## Backend

`GET /dash/audit-log?limit=N&action=<substring>` — unauthenticated read,
tailing whatever `AuditLogger` has written to `computer_use.audit_log_path`
(JSON-Lines, batched flush, redacted).

## Format

```jsonl
{"ts":1724512345.12,"action":"shell_run","cmd":["git","status"],"status":"success","duration_ms":42}
{"ts":1724512346.01,"action":"shell_run","cmd":["rm","-rf","/"],"status":"denied","reason":"first-token not allowlisted"}
```

Approval denials and timeouts get dedicated entries
(`shell_run.denied`, `fs_write.timed_out`, …) carrying the `approval_id` and
operator reason — see [Approvals](approvals.md). Sensitive keys (`token`,
`password`, `secret`, `api_key`, `webhook_url`, `authorization`, `cookie`,
`private_key`) are redacted to `***redacted***` before the line is written,
both here and in structured logs.
