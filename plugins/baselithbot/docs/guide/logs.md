# Live Logs

A real-time view of the dashboard's Server-Sent Events bus — every event
every other page consumes, in one filterable stream.

## What the page does

- Connects to `GET /dash/events/stream` and shows up to the last 500 events
  received this session, most recent first.
- **Type filter** — a dropdown of every event type seen so far
  (`run.step`, `session.message`, `approval.pending`, `models.updated`,
  `cron.removed`, `node.token_issued`, `computer_use.updated`,
  `stealth.updated`, …).
- **Search** — substring match across the event type and its JSON payload.
- A connection-state pill (`open` / `connecting` / `error`) so you can tell
  a quiet stream from a broken one.

## Backend

`GET /dash/events/recent` (buffered history, bounded 200-event ring) and
`GET /dash/events/stream` (live SSE). Every event is dual-emitted (a named
frame and a default-message frame) so a consumer can either listen
wildcard-style (`onmessage`, filtering on the decoded `type`) or attach a
type-specific `addEventListener` — not both on the same connection, or each
event arrives twice.

## When to use it

Logs is the fastest way to confirm a write actually happened — e.g. after
saving [Computer Use](computer-use.md) policy, watch for
`computer_use.updated` here instead of round-tripping to that page's read
endpoint. It complements the [Audit Log](audit-log.md), which is a durable,
security-focused trail rather than an ephemeral operational feed.
