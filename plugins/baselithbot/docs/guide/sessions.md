# Sessions

Bounded, named conversation histories for the agent — the same primitive
messaging channels and MCP tools use to keep a coherent back-and-forth.

## What it does

- **Create** a session with a title and an optional "primary" flag.
- **List / filter / sort** sessions by recent activity, title, or creation
  time; each row shows message count and last-active time.
- **History panel** — full message list for the selected session, its
  sandbox info (if a Docker sandbox is attached — see
  [Subsystems](../reference/architecture.md)), recent live activity scoped
  to that session, and a linked task run card if the conversation triggered
  a browser task.
- **Send** — plain text launches a linked autonomous browser task (routed
  the same way as [Run Task](run-task.md)); a `/command` line executes
  inline through the chat-command router (`/status`, `/new`, `/reset`,
  `/compact`, `/think`, `/verbose`, `/trace`, `/usage`, `/restart`,
  `/activation`) and appends its result to the same history.
- **Reset** clears history (with confirmation); **Delete** removes the
  session entirely (with confirmation).

## Backend

`GET/POST /dash/sessions`, `GET /dash/sessions/{sid}/history`,
`POST /dash/sessions/{sid}/send` (🔒), `POST /dash/sessions/{sid}/reset`
(🔒), `DELETE /dash/sessions/{sid}` (🔒). Write routes rate-limited (30/min
for create/send, 20/min for reset/delete). Session mutations broadcast on
the SSE bus (`session.created` / `session.message` / `session.reset` /
`session.deleted`) so the panel updates live without polling once the SSE
connection is open.

## Notes

Sessions are process-local, in-memory histories (`SessionManager`) — they
are not the same as [replay](replay.md) records, which persist every step
of a *browser run* to SQLite regardless of which session triggered it.
