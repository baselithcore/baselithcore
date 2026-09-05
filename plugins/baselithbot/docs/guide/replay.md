# Replay

Time-travel debugger for browser runs — scrub step-by-step through any
recorded run's screenshots, reasoning, and extracted data.

## What the page does

- **Run catalog** — every recorded run (most recent first), with status
  (`running` / `completed` / `failed`) and step count.
- **Run summary** — goal, start URL, final URL, timing, error (if any).
- **Step viewer** — forward/back through each captured step: the action
  taken, the model's reasoning text, the current URL at that point, and the
  screenshot as it looked at that moment.
- **Follow live** — while a run is still `running`, the viewer can track the
  newest step automatically (polls every 3s for a running run, otherwise on
  demand).
- **Command panel** for quick actions (e.g. jump to first/last step).

## Backend

`GET /dash/replay/runs?limit=N` (list), `GET /dash/replay/runs/{run_id}`
(full run + steps), `GET
/dash/replay/runs/{run_id}/steps/{step_index}/screenshot` (fetch one
screenshot on demand — list views can request `include_screenshots=false`
to skip hydrating every blob up front).

## Storage & retention

Every step of every run — action, reasoning, URL, screenshot, extracted
data — persists to SQLite (`plugins/baselithbot/.state/replay.sqlite`).
Retention is 14 days by default via a cron job (`replay.prune_history`, see
[Cron](cron.md)). If `BASELITHBOT_REPLAY_ENCRYPTION_KEY` is set, screenshots
are encrypted at rest with Fernet and decrypted transparently on read — a
screenshot on disk without an available key is refused rather than served,
never silently returned as ciphertext.

## Tenancy

Runs are the one place in BaselithBot where per-caller scoping is applied —
see [Multi-tenancy](../reference/tenancy.md) for exactly how and why.
