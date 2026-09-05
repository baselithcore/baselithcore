# Metrics

LLM usage/cost telemetry and the raw Prometheus exposition, in one view.

## What the page does

- **Stat cards** — summary totals from the `UsageLedger` (tokens, cost,
  event count, average latency).
- **Usage trend panel** — the same recent-events chart shown on
  [Overview](overview.md), independently filterable here.
- **By-model breakdown** — searchable, sortable (tokens / cost / event
  count) table of spend per `(provider, model)` pair.
- **Prometheus panel** — the raw text exposition from
  `/baselithbot/metrics`, expandable inline for quick inspection without
  leaving the dashboard.

## Backend

`GET /dash/usage/summary`, `GET /dash/usage/recent?limit=N`,
`GET /dash/metrics/prometheus` (JSON-wrapped passthrough of the same series
served at `GET /baselithbot/metrics` for real scrapers).

## Notable series

| Series | Type | Labels | Meaning |
|---|---|---|---|
| `baselithbot_inbound_event_total` | Counter | `channel` | Inbound webhook events received |
| `baselithbot_run_total` | Counter | `result` | `POST /run` outcomes (`success`/`failed`/`error`) |
| `baselithbot_run_steps` | Histogram | — | Steps taken per run |
| `baselithbot_tool_errors_total` | Counter | `tool` | Tool-level error counter |

See [Operations runbook](../operations/runbook.md) for a scrape config and
alert-rule examples.
