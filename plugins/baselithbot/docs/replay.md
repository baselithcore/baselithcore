# Task replay (time-travel debug)

[← Index](./README.md)

Source of truth: [`replay.py`](../replay.py).
Dashboard surface: [`dashboard/routes/replay.py`](../dashboard/routes/replay.py),
UI page [`ui/src/pages/Replay.tsx`](../ui/src/pages/Replay.tsx).

## 1. Why

Agent runs are non-deterministic. When something looks wrong in the
output, the operator needs to inspect *every* reasoning step and the
matching screenshot to diagnose the failure. `TaskReplayStore` persists
each Observe → Plan → Act step into SQLite so the dashboard can scrub
forward/back through the run like a video player.

## 2. Storage

- Path: `<state>/replay.sqlite` (default:
  `plugins/baselithbot/.state/replay.sqlite`, git-ignored).
- WAL journal mode + `synchronous=NORMAL` — safe concurrent writes from
  the async agent loop and dashboard reads.
- Screenshots: base64 PNG strings produced by the browser agent, stored
  verbatim. For most runs this is the dominant payload.
- Retention: a 6-hour cron job (`replay.prune_history`, registered in
  [`_bootstrap.py`](../_bootstrap.py)) evicts runs whose `started_at` is
  older than 14 days.

## 3. Schema

```sql
CREATE TABLE runs (
    run_id         TEXT PRIMARY KEY,
    goal           TEXT NOT NULL,
    start_url      TEXT,
    max_steps      INTEGER,
    status         TEXT,             -- 'running' | 'completed' | 'failed'
    started_at     REAL NOT NULL,
    completed_at   REAL,
    final_url      TEXT,
    error          TEXT,
    extracted_json TEXT              -- final extracted dict (JSON)
);

CREATE TABLE steps (
    run_id         TEXT NOT NULL,
    step_index     INTEGER NOT NULL,
    ts             REAL NOT NULL,
    action         TEXT,
    reasoning      TEXT,
    current_url    TEXT,
    screenshot_b64 TEXT,
    extracted_json TEXT,
    PRIMARY KEY (run_id, step_index)
);
```

## 4. Write path

[`router.py`](../router.py) `POST /baselithbot/run` handler wires the
recorder automatically:

- `plugin.replay.start_run(...)` at task launch
- `plugin.replay.add_step(...)` inside the existing `_on_progress`
  callback fired by `BaselithbotAgent.execute`
- `plugin.replay.finish_run(...)` on success, failure, or exception

No additional instrumentation required in agent code — the callback
already carries `steps_taken`, `action`, `reasoning`, `current_url`,
`last_screenshot_b64`, and the running `extracted_data`.

## 5. REST routes (`/baselithbot/dash/*`)

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/dash/replay/runs?limit=N` | 🔓 | List persisted runs (default 50, max 500) |
| GET | `/dash/replay/runs/{run_id}` | 🔓 | Full run detail with every step |

Response shape (`GET /dash/replay/runs/{run_id}`):

```json
{
  "run": {
    "run_id": "run-abc123",
    "goal": "Find the latest React release",
    "start_url": "https://react.dev",
    "max_steps": 20,
    "status": "completed",
    "started_at": 1724512345.12,
    "completed_at": 1724512389.02,
    "final_url": "https://react.dev/blog",
    "error": null,
    "extracted_data": {"version": "19.0.0"},
    "steps": [
      {
        "step_index": 1,
        "ts": 1724512346.01,
        "action": "navigate",
        "reasoning": "Landing page",
        "current_url": "https://react.dev",
        "screenshot_b64": "iVBOR…",
        "extracted_data": {}
      },
      …
    ]
  }
}
```

## 6. UI: Replay page

Split-pane at `/baselithbot/replay`:

- **Run list** (left) — auto-refresh every 5s, shows truncated run_id,
  goal preview, status badge, step count, relative age.
- **Scrubber** (right) — range slider + ◀ / ▶ buttons, large screenshot
  on the left, side panel with action, reasoning, current URL,
  extracted data snapshot, and capture timestamp.

## 7. Callable Python API

```python
from plugins.baselithbot.replay import TaskReplayStore

store = TaskReplayStore("/tmp/replay.sqlite")
store.start_run(run_id="r1", goal="demo", start_url=None, max_steps=5)
store.add_step(
    run_id="r1", step_index=1,
    action="navigate", reasoning="go",
    current_url="https://ex.com",
    screenshot_b64=None,
    extracted_data={},
)
store.finish_run(run_id="r1", success=True, final_url="...", error=None, extracted_data={})

store.list_runs(limit=10)           # dashboard listing
store.get_run("r1")                 # full detail
store.prune_older_than(retention_seconds=14 * 86400)
```

## 8. Testing

Coverage: [`tests/unit/plugins_tests/test_baselithbot_replay_som.py`](../../../tests/unit/plugins_tests/test_baselithbot_replay_som.py)
— start/add/finish/list/get, unknown-run 404, prune-older-than,
plugin-level integration.
