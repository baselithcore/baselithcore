# HTTP API reference

[← Index](./README.md)

All routes mounted under **`/baselithbot`**. Write endpoints require
dashboard bearer token when `BASELITHBOT_DASHBOARD_TOKEN` is set; reads
stay open. Rate limits per client IP via
[`policies/rate_limit.py`](../policies/rate_limit.py).

See [dashboard.md](./dashboard.md) for `/dash/*` routes.
See [security.md](./security.md) for auth + rate-limit table.

## 1. Core routes

| Method | Path | Auth | RL | Body / Response |
|--------|------|------|----|-----------------|
| GET | `/baselithbot/` | — | — | 307 → `/baselithbot/ui/` |
| POST | `/baselithbot/run` | token | 10/min | `RunRequest` → `BaselithbotResult` |
| GET | `/baselithbot/status` | — | — | `StatusResponse` |
| POST | `/baselithbot/inbound/{channel}` | — | body≤1 MiB | `{status, channel, results}` |
| WS | `/baselithbot/ws/pair` | token in handshake | 20/min | `{token, node_id, platform}` → `{status:"paired", node}` |
| GET | `/baselithbot/metrics` | — | — | Prometheus text exposition |

## 2. `POST /baselithbot/run`

Request body — [`RunRequest`](../router.py):

```json
{
  "run_id": "optional-string",
  "goal": "open hacker news and list top 3 stories",
  "start_url": "https://news.ycombinator.com",
  "max_steps": 20,
  "extract_fields": ["title", "url"]
}
```

Response — `BaselithbotResult`:

```json
{
  "run_id": "run-1f6a…",
  "success": true,
  "final_url": "https://…",
  "steps_taken": 7,
  "extracted_data": {"title": "[extracted from …]"},
  "history": ["navigate: …", "click: …", "done: …"],
  "error": null,
  "last_screenshot_b64": "iVBOR…"
}
```

Field constraints:

| Field | Rule |
|-------|------|
| `goal` | 1–4000 chars, required |
| `max_steps` | 1–100 |
| `extract_fields` | optional list of field names |

Status codes:

| Code | Cause |
|------|-------|
| 200 | Task completed (check `success` flag) |
| 401 | Missing bearer token |
| 403 | Invalid bearer token |
| 429 | Rate limit exceeded |
| 500 | Unhandled exception — `error` contains message |

Progress callback fired per step via the dashboard event bus (`run.started`,
`run.step`, `run.completed`/`run.failed`). UI consumers listen on
`GET /dash/events/stream` — see [dashboard.md](./dashboard.md).

## 3. `GET /baselithbot/status`

Returns `StatusResponse`:

```json
{
  "state": "ready",
  "backend_started": true,
  "stealth_enabled": true
}
```

`state` ∈ `{uninitialized, starting, ready, stopping, stopped}`.

## 4. `POST /baselithbot/inbound/{channel}`

Accepts raw provider payload (Slack events API, Telegram update, Discord
interaction, or anything through `parse_generic`). Path segment
`{channel}` selects the parser.

Processing pipeline:

1. Body size check — reject > 1 MiB with `413`.
2. JSON decode; malformed → `{"raw": "…"}`.
3. Normalize to `InboundEvent` via [`inbound/parsers.py`](../inbound/parsers.py).
4. `DMPairingPolicy.evaluate()` — DM from unpaired sender → `{"status": "denied", "reason": …}`.
5. Prometheus counter `baselithbot_inbound_event_total{channel}`.
6. `InboundDispatcher.dispatch()` → registered handlers.

Response:

```json
{"status": "received", "channel": "slack", "results": [...]}
```

## 5. `WS /baselithbot/ws/pair`

Node pairing WebSocket handshake.

```text
← client connects
→ client sends: {"token": "<pairing_token>", "node_id": "edge-01", "platform": "linux"}
← server:
    ok   → {"status": "paired", "node": {...}}
    bad  → {"status": "error", "error": "..."}, close 4000
    rate → close 4290 "rate limit exceeded"
```

After handshake: server echoes any text frame as `"ack: <first 200 chars>"`
until disconnect. Command family routing lives in
[`nodes/commands.py`](../nodes/commands.py).

Issue pairing token: `POST /dash/nodes/token` (bearer-gated).

## 6. `GET /baselithbot/metrics`

Prometheus exposition (`text/plain; version=0.0.4`). See
[observability.md](./observability.md) for series list.

## 7. Static UI

`GET /baselithbot/ui/{path:path}` serves [`ui/dist/`](../ui/dist/). Unknown
paths fall back to `index.html` (React Router client-side routing). When
the bundle is missing: graceful 503 with build instructions.

All responses carry hardened headers:

```text
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: no-referrer
Permissions-Policy: microphone=(), camera=(), geolocation=()
```

## 8. Error envelope conventions

- Typed HTTP exceptions → standard FastAPI `{"detail": "..."}` body.
- Tool / handler errors → `{"status": "error", "error": "..."}` — never
  raise to orchestrator.
- Capability denials → `{"status": "denied", "error": "..."}`.
