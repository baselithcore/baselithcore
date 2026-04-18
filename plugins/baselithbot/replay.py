"""Persistent task-replay store backed by SQLite.

Captures every ``on_progress`` step emitted by ``BaselithbotAgent.execute`` —
action, reasoning, current URL, screenshot, and extracted data — so the
dashboard can scrub back through past runs step-by-step.

Schema
------
runs (run_id, goal, start_url, max_steps, status, started_at, completed_at,
      final_url, error, extracted_json)
steps (run_id, step_index, ts, action, reasoning, current_url,
       screenshot_b64, extracted_json)

Retention
---------
Callers should prune via ``prune_older_than`` from a cron job
(default retention wired as 14 days). Screenshots are the biggest payload;
they are stored verbatim as base64 text — agent.py produces them in that
form already.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    goal TEXT NOT NULL,
    start_url TEXT,
    max_steps INTEGER,
    status TEXT,
    started_at REAL NOT NULL,
    completed_at REAL,
    final_url TEXT,
    error TEXT,
    extracted_json TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    ts REAL NOT NULL,
    action TEXT,
    reasoning TEXT,
    current_url TEXT,
    screenshot_b64 TEXT,
    extracted_json TEXT,
    PRIMARY KEY (run_id, step_index)
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, step_index);
"""


class TaskReplayStore:
    """SQLite-backed recorder for agent run steps."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def start_run(
        self,
        *,
        run_id: str,
        goal: str,
        start_url: str | None,
        max_steps: int,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs "
                "(run_id, goal, start_url, max_steps, status, started_at) "
                "VALUES (?, ?, ?, ?, 'running', ?)",
                (run_id, goal, start_url, max_steps, time.time()),
            )

    def add_step(
        self,
        *,
        run_id: str,
        step_index: int,
        action: str,
        reasoning: str,
        current_url: str,
        screenshot_b64: str | None,
        extracted_data: dict[str, Any],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO steps "
                "(run_id, step_index, ts, action, reasoning, current_url, "
                " screenshot_b64, extracted_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    step_index,
                    time.time(),
                    action,
                    reasoning,
                    current_url,
                    screenshot_b64,
                    json.dumps(extracted_data, default=str),
                ),
            )

    def finish_run(
        self,
        *,
        run_id: str,
        success: bool,
        final_url: str,
        error: str | None,
        extracted_data: dict[str, Any],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE runs SET status=?, completed_at=?, final_url=?, "
                "error=?, extracted_json=? WHERE run_id=?",
                (
                    "completed" if success else "failed",
                    time.time(),
                    final_url,
                    error,
                    json.dumps(extracted_data, default=str),
                    run_id,
                ),
            )

    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT r.run_id, r.goal, r.start_url, r.status, r.started_at, "
                "r.completed_at, r.final_url, r.error, "
                "(SELECT COUNT(*) FROM steps s WHERE s.run_id = r.run_id) AS step_count "
                "FROM runs r ORDER BY r.started_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            run_row = conn.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run_row is None:
                return None
            step_rows = conn.execute(
                "SELECT step_index, ts, action, reasoning, current_url, "
                "screenshot_b64, extracted_json "
                "FROM steps WHERE run_id=? ORDER BY step_index",
                (run_id,),
            ).fetchall()
        run = dict(run_row)
        if run.get("extracted_json"):
            try:
                run["extracted_data"] = json.loads(run["extracted_json"])
            except json.JSONDecodeError:
                run["extracted_data"] = {}
        else:
            run["extracted_data"] = {}
        run.pop("extracted_json", None)
        steps: list[dict[str, Any]] = []
        for row in step_rows:
            step = dict(row)
            if step.get("extracted_json"):
                try:
                    step["extracted_data"] = json.loads(step["extracted_json"])
                except json.JSONDecodeError:
                    step["extracted_data"] = {}
            else:
                step["extracted_data"] = {}
            step.pop("extracted_json", None)
            steps.append(step)
        run["steps"] = steps
        return run

    def prune_older_than(self, *, retention_seconds: float) -> int:
        """Drop runs (and their steps) whose ``started_at`` is older than cutoff.

        A non-positive ``retention_seconds`` deletes every recorded run.
        """
        cutoff = time.time() - float(retention_seconds)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id FROM runs WHERE started_at < ?", (cutoff,)
            ).fetchall()
            run_ids = [row["run_id"] for row in rows]
            if not run_ids:
                return 0
            placeholders = ",".join("?" for _ in run_ids)
            conn.execute(
                f"DELETE FROM steps WHERE run_id IN ({placeholders})",
                run_ids,
            )
            conn.execute(
                f"DELETE FROM runs WHERE run_id IN ({placeholders})",
                run_ids,
            )
        logger.info("baselithbot_replay_pruned", runs=len(run_ids))
        return len(run_ids)


__all__ = ["TaskReplayStore"]
