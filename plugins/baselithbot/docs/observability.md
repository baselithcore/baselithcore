# Observability

[← Index](./README.md)

Prometheus metrics, audit log, structured logs, OpenTelemetry traces,
dashboard SSE event bus.

## 1. Prometheus

`GET /baselithbot/metrics` renders the registry. JSON passthrough at
`/baselithbot/dash/metrics/prometheus` for dashboard rendering.

Notable series ([`metrics.py`](../metrics.py)):

| Series | Type | Labels | Meaning |
|--------|------|--------|---------|
| `baselithbot_inbound_event_total` | Counter | `channel` | Inbound webhook events received |
| `baselithbot_run_total` | Counter | `result` (`success`/`failed`/`error`) | `POST /run` outcomes |
| `baselithbot_run_steps` | Histogram | — | Steps taken per run |
| `baselithbot_tool_errors_total` | Counter | `tool` | Tool-level error counter |

Plus standard FastAPI/uvicorn process metrics when the core observability
bundle is wired.

### 1.1 Recommended alerts

```yaml
- alert: BaselithbotShellErrorsSpiking
  expr: rate(baselithbot_tool_errors_total{tool="shell_run"}[5m]) > 0.1
  for: 10m
  annotations:
    summary: "Shell tool errors elevated — review audit log"

- alert: BaselithbotRunFailureRate
  expr: |
    sum(rate(baselithbot_run_total{result!="success"}[15m]))
      / sum(rate(baselithbot_run_total[15m])) > 0.5
  for: 15m
  annotations:
    summary: "Run failure rate > 50% for 15m"
```

## 2. Audit log

`AuditLogger` writes JSON-Lines to `audit_log_path`. Each line:

```json
{"ts": 1724512345.12, "action": "shell_run", "cmd": ["git","status"], "status": "success", "duration_ms": 42}
```

Retention posture: ship to Loki / CloudWatch Logs via Promtail / Fluent
Bit with immutable retention. Secrets redacted via
[`secret_redaction.py`](../secret_redaction.py) before write.

See [computer-use.md §3](./computer-use.md#3-audit-log-format).

## 3. Structured logs

All logs flow through `core.observability.logging.get_logger`. Every
event uses `snake_case` keys so they grep cleanly across plugins:

| Event | Fields |
|-------|--------|
| `baselithbot_started` | `agent_id`, `headless`, `stealth` |
| `baselithbot_stopped` | `agent_id` |
| `baselithbot_step` | `step`, `action`, `url` |
| `baselithbot_tool_error` | `tool`, `error` |
| `baselithbot_openclaw_tool_error` | `tool`, `error` |
| `baselithbot_extra_tool_error` | `tool`, `error` |
| `baselithbot_dashboard_open` | `reason` (one-shot dev-mode warning) |
| `baselithbot_flow_handle_browse` | `goal_preview`, `start_url`, `max_steps` |
| `baselithbot_plugin_initialized` | `config_keys` |
| `baselithbot_model_prefs_loaded` / `_updated` | `path` / `provider` + `model` + `vision_provider` |
| `baselithbot_cron_stop_failed` / `_channels_shutdown_failed` | `error` |

Secret keys redacted automatically.

## 4. Traces (OpenTelemetry)

[`tracing.py`](../tracing.py) is a thin shim that no-ops when
OTEL is disabled. When `core.observability.tracing` is wired, spans are
emitted for:

- `baselithbot.agent.execute` — wraps entire run
- `baselithbot.agent.step` — one Observe/Plan/Act iteration
- `baselithbot.tool.<name>` — tool invocation
- `baselithbot.inbound.dispatch` — inbound event dispatch

Span attributes mirror the structured-log keys for cross-correlation.

## 5. Dashboard event bus (SSE)

Process-wide `DashboardEventBus`:

- Bounded 200-event ring buffer for history replay.
- 256-deep per-subscriber `asyncio.Queue`; slow consumers dropped
  (non-fatal — preserves producer latency).
- Initial frame: `": connected\n\n"` (comment heartbeat).

Event catalog:

| Event | Payload |
|-------|---------|
| `run.started` | `run_id`, `goal`, `max_steps`, `start_url` |
| `run.step` | `run_id`, `steps_taken`, `action`, `reasoning`, `current_url` |
| `run.completed` / `run.failed` | `run_id`, `steps_taken`, `final_url`, `error`, `status` |
| `session.created` / `session.message` / `session.reset` / `session.deleted` | Session snapshot or id |
| `cron.removed` | `name` |
| `node.token_issued` | `platform` |
| `node.revoked` | `node_id` |
| `models.updated` | `provider`, `model`, `vision_provider`, `vision_model` |

Consumer example (TypeScript):

```ts
const es = new EventSource("/baselithbot/dash/events/stream");
es.addEventListener("run.step", (e) => {
  const { payload } = JSON.parse((e as MessageEvent).data);
  console.log(payload.steps_taken, payload.action);
});
```

## 6. Doctor

[`doctor.py`](../doctor.py) probes:

- Python version against the 3.10–3.12 band
- Playwright install + Chromium availability
- `pyautogui` import + platform backend
- `mss` + `Pillow`
- Docker daemon reachable (for session sandboxing)
- Tailscale CLI on `$PATH`
- macOS Accessibility / Screen Recording permission hints

Returns structured report consumed by `/dash/doctor` and the dashboard
`Doctor.tsx` page.

## 7. Run tracker (per-run state)

[`run_tracker.py`](../run_tracker.py) — bounded ring of live run states.
Populated per step by `router.run()` and served via:

- `GET /dash/run-task/latest`
- `GET /dash/run-task/recent?limit=N` (default 8)
- `GET /dash/run-task/{run_id}`

Each run state exposes goal, max_steps, steps_taken, last action,
reasoning, current_url, history, extracted_data, last_screenshot_b64,
status, error.

## 8. Prometheus scrape example

```yaml
scrape_configs:
  - job_name: baselithbot
    metrics_path: /baselithbot/metrics
    static_configs:
      - targets: ["localhost:8000"]
```
