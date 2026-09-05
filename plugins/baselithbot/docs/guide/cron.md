# Cron

The plugin's async job scheduler — built-in maintenance jobs plus
operator-defined custom jobs, all polled once a second by `CronScheduler`.

## What the page does

- **Job list** — search, filter by status, sort (next run / name /
  status); each row shows the standard 5-field crontab expression, enabled
  state, last error (if any), and next scheduled fire time.
- **Detail drawer** — inspect a job, edit its interval, toggle
  enabled/disabled, or trigger a manual **run now**.
- **Create** a custom job from the catalog of available handlers.
- **Remove** a custom job.

Built-in jobs registered at startup include the replay-store retention
sweep (`replay.prune_history`, 14-day default) referenced in
[Replay](replay.md).

## Backend

`GET /dash/crons`, `GET /dash/crons/catalog`, `GET /dash/crons/custom`,
`POST /dash/crons` (🔒, create), `PUT /dash/crons/{name}/custom` (🔒),
`PATCH /dash/crons/{name}` (🔒, e.g. interval edit),
`POST /dash/crons/{name}/toggle` (🔒), `POST /dash/crons/{name}/run` (🔒,
manual trigger), `POST /dash/crons/{name}/remove` (🔒, 20/min — publishes
`cron.removed` on the SSE bus).

## Notes

Custom cron jobs persist to `plugins/baselithbot/.state/custom_crons.json`
and are restored (`bootstrap()`) on plugin startup. The cron backend label
(currently `"asyncio"`) is surfaced on both [Overview](overview.md) and this
page.
