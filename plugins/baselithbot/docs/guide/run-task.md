# Run Task

Dispatch and watch a single autonomous browser run — the primary way to
drive the Observe → Plan → Act loop from the dashboard.

## What it does

- A form to submit a goal (required, 1–4000 chars), optional start URL, max
  steps (1–100, default 20), and a comma-separated list of fields to
  extract.
- A **live run panel** that tracks the currently selected run's progress bar
  (`steps_taken / max_steps`), current screenshot, and error state, updated
  via the dashboard SSE bus (`run.started` / `run.step` / `run.completed` /
  `run.failed`).
- A **timeline** of only the events belonging to the selected run.
- **Recent runs** — the last runs from the bounded `RunTracker` history,
  selectable to inspect any of them.
- **History & extracted data** panel for the selected run once it has
  progressed.
- The selected run id is reflected in the URL (`?run=<id>`) so a run can be
  linked to directly, including from [Sessions](sessions.md) (a session
  linked to a task run shows an "Open linked run" shortcut here).

## Backend

`POST /baselithbot/run` (bearer-token gated, 10/min per client IP) dispatches
the task and persists per-step snapshots into the
[replay store](replay.md); `GET /dash/run-task/latest`,
`GET /dash/run-task/recent?limit=N`, and `GET /dash/run-task/{run_id}` read
back `RunTracker` state (all unauthenticated reads).

## Notes

- The singleton browser agent starts lazily on the first run — expect a
  short delay while Chromium launches.
- `N` is a keyboard shortcut to start a new task (ignored while typing in a
  field); dispatching a task while one is still pending prompts for
  confirmation since the running task continues server-side regardless.
- Every step is captured for playback — see [Replay](replay.md).
