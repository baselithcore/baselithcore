# Approvals

The human-in-the-loop queue for privileged Computer Use actions — the
operator-facing half of `require_approval_for` (see
[Computer Use](computer-use.md)).

## Why

Privileged actions (`shell_run`, `fs_write`, `mouse_click`, `kbd_type`, …)
can move fast inside an autonomous loop. When a capability is listed in
`require_approval_for`, every invocation parks in `ApprovalGate` instead of
running immediately — the agent submits, the dashboard shows the full
parameters plus an expiry countdown, and an operator approves or denies
before the action proceeds.

## Lifecycle

```text
pending ──approve──▶ approved   (action runs)
   │                              │
   ├──deny────────▶ denied        ▼  ComputerUseError raised,
   │                              │  action skipped, audit logged
   └──timeout────▶ timed_out ◄────┘
```

`approval_timeout_seconds` (default 120, clamp 1–3600) governs how long a
request stays pending before auto-denial.

## What the page does

- **Stat grid** — pending count, resolved-today, and similar totals.
- **Pending queue** — one card per request: capability, action, JSON
  parameters, a countdown pill (amber under 30s, red under 10s), a reason
  field, and Approve/Deny buttons. Polls every 2.5s.
- **History list** — last 50 resolutions with status badges.
- **Request drawer** for full parameter inspection.
- **Policy panels** — a read-only reminder of which capabilities currently
  require approval and the timeout, linking back to
  [Computer Use](computer-use.md) to change them.

## Backend

`GET /dash/approvals` (pending + last-50 history), `POST
/dash/approvals/{id}/approve` (🔒, 5/min, optional `reason` ≤500 chars),
`POST /dash/approvals/{id}/deny` (🔒, 5/min). All four transitions broadcast
on the SSE bus: `approval.pending`, `approval.resolved`,
`approval.approved`, `approval.denied`.

## Notes

`ApprovalGate` is process-local and asyncio-native — it is not a durable
queue, so a process restart drops any pending request (the underlying
Computer Use call then observes a `ComputerUseError` from the timeout/abort
path). Every resolution — approved or not — is written to the
[audit log](audit-log.md).
