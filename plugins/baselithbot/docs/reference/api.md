# API

All routes mount under **`/baselithbot`**. Write endpoints require the
dashboard bearer token when `BASELITHBOT_DASHBOARD_TOKEN` is set; reads stay
open. Rate limits are per-client-IP — see [Security & RBAC](security.md)
for the full table and the `X-Forwarded-For` requirement behind a proxy.

## Core routes

| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/baselithbot/` | — | 307 → `/baselithbot/ui/` |
| POST | `/baselithbot/run` | 🔒 10/min | `RunRequest` → `BaselithbotResult` |
| GET | `/baselithbot/status` | — | `StatusResponse` |
| POST | `/baselithbot/inbound/{channel}` | — (body ≤ 1 MiB) | `{status, channel, results}` |
| WS | `/baselithbot/ws/pair` | token in handshake, 20/min | `{token, node_id, platform}` → `{status:"paired", node}` |
| GET | `/baselithbot/metrics` | — | Prometheus text exposition |
| GET | `/baselithbot/ui/{path}` | — | Static SPA bundle (503 placeholder if unbuilt) |

### `POST /baselithbot/run`

```json
{
  "run_id": "optional-string",
  "goal": "open hacker news and list top 3 stories",
  "start_url": "https://news.ycombinator.com",
  "max_steps": 20,
  "extract_fields": ["title", "url"]
}
```

`goal` 1–4000 chars required; `max_steps` 1–100. Response is
`BaselithbotResult` (`run_id`, `success`, `final_url`, `steps_taken`,
`extracted_data`, `history`, `error`, `last_screenshot_b64`). Progress is
pushed via the SSE bus (`run.started` / `run.step` / `run.completed` /
`run.failed`), consumed by [Run Task](../guide/run-task.md).

### `GET /baselithbot/status`

```json
{"state": "ready", "backend_started": true, "stealth_enabled": true}
```

`state` ∈ `{uninitialized, starting, ready, stopping, stopped}`.

### `POST /baselithbot/inbound/{channel}`

Accepts a raw provider payload (Slack events API, Telegram update, Discord
interaction, or anything via the generic parser). Pipeline: 1 MiB body cap
→ JSON decode (malformed → `{"raw": "…"}`) → normalize to `InboundEvent` →
`DMPairingPolicy.evaluate()` (unpaired DM → `{"status":"denied", ...}`) →
Prometheus counter → `InboundDispatcher.dispatch()`. See
[Channels](../guide/channels.md).

### `WS /baselithbot/ws/pair`

```text
← client connects
→ client sends: {"token": "<pairing_token>", "node_id": "edge-01", "platform": "linux"}
← server: {"status": "paired", "node": {...}}   |   close 4000 (bad handshake)   |   close 4290 (rate limit)
```

Issue a pairing token via `POST /dash/nodes/token` (🔒). See
[Nodes](../guide/nodes.md).

## Dashboard API (`/baselithbot/dash/*`)

One router per surface under `dashboard/routes/`, composed in
`dashboard/app.py`. Every write route (🔒) sits behind the same
`DashboardAuth` guard as the core routes above; every GET listed here is an
unauthenticated read.

| Surface | Routes |
|---|---|
| Overview / usage | `GET /overview`, `GET /doctor`, `GET /canvas`, `GET /usage/summary`, `GET /usage/recent`, `GET /workspaces`, `GET /metrics/prometheus` |
| Run Task | `GET /run-task/latest`, `GET /run-task/recent`, `GET /run-task/{run_id}` |
| Sessions | `GET/POST /sessions` 🔒post, `GET /sessions/{sid}/history`, `POST /sessions/{sid}/send` 🔒, `POST /sessions/{sid}/reset` 🔒, `DELETE /sessions/{sid}` 🔒 |
| Channels | `GET/PUT/DELETE /channels/{name}/config` 🔒write, `POST /channels/{name}/start\|stop\|test` 🔒 |
| Skills / cron / nodes | `registry.py` — `GET /channels`, `GET/PUT /skills*` 🔒write, `GET/POST/PUT/PATCH /crons*` 🔒write, `GET/POST/DELETE /nodes*` 🔒write |
| Models / keys | `GET/PUT /models` 🔒put, `GET/PUT/DELETE /provider-keys/{provider}` 🔒write, `POST /provider-keys/{provider}/test` 🔒 |
| Agents | `GET /agents`, `GET /agents/catalog`, `POST/PUT/DELETE /agents*` 🔒, `POST /agents/{name}/dispatch` 🔒 |
| Workspaces | `POST/PUT/DELETE /workspaces*` 🔒 |
| Canvas | `POST /canvas/render\|clear\|dispatch` 🔒 |
| Computer Use / Stealth | `GET/PUT /computer-use` 🔒put, `GET/PUT /stealth` 🔒put |
| Desktop Task | `GET /desktop/tools`, `POST /desktop/tools/{tool_name}` 🔒, `POST /desktop/task` 🔒, `POST /desktop/task/{run_id}/cancel` 🔒, `GET /desktop/task/latest\|recent\|{run_id}` |
| Approvals | `GET /approvals`, `POST /approvals/{id}/approve\|deny` 🔒 |
| Audit log | `GET /audit-log` |
| Replay | `GET /replay/runs`, `GET /replay/runs/{run_id}`, `GET /replay/runs/{run_id}/steps/{step_index}/screenshot` |
| Events | `GET /events/recent`, `GET /events/stream` (SSE) |

Each surface's endpoints, payloads, and gotchas are documented on its guide
page under **User guide**.

## SSE event catalog

| Event | Payload |
|---|---|
| `run.started` / `run.step` / `run.completed` / `run.failed` | `run_id`, goal/steps/action/reasoning/current_url/final_url/error |
| `session.created` / `session.message` / `session.reset` / `session.deleted` | Session snapshot or id |
| `cron.removed` | `name` |
| `node.token_issued` / `node.revoked` | `platform` / `node_id` |
| `computer_use.updated` / `stealth.updated` | Policy-summary payload |
| `approval.pending` / `approval.resolved` / `approval.approved` / `approval.denied` | `ApprovalRequest` snapshot |
| `models.updated` | `provider`, `model`, `vision_provider`, `vision_model` |

Each event is dual-emitted (named frame + default-message frame) — pick one
consumption style per subscriber. See [Live Logs](../guide/logs.md).

## Error envelope conventions

- Typed HTTP exceptions → standard FastAPI `{"detail": "..."}` body.
- Tool / handler errors (MCP tools, Computer Use) → `{"status": "error",
  "error": "..."}` — never raised to the orchestrator.
- Capability denials → `{"status": "denied", "error": "..."}`.

## MCP tools (37+)

Every tool registered via `get_mcp_tools()` returns one of the three status
envelopes above and never raises. Groups: **Browser (7)** — navigate,
click, type, scroll, screenshot, `eval_js_safe` (sanitized via
`core.services.sanitization.InputSanitizer`), `run_task`. **Computer Use
(12)** — desktop screenshot/screen size, mouse, keyboard, shell (allowlist

- `allow_shell` gated), filesystem (root + `allow_filesystem` gated).
**OpenClaw parity (17)** — channels, sessions, chat commands, doctor,
skills, voice TTS, canvas render, cron list, Tailscale status, node
pairing. **Extras** — code editing (diff apply, line edit, search/replace,
multi-file), usage ledger, workspace lifecycle, agent routing, process
control, Tailscale provisioning. **Set-of-Mark** —
`baselithbot_som_annotate` injects numbered overlay boxes on clickable
elements so a vision LLM can reference marks instead of raw coordinates.
