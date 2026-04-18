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
they may contain sensitive on-screen information (credentials visible on
the browser), so when ``BASELITHBOT_REPLAY_ENCRYPTION_KEY`` is set the
store encrypts them at rest via :mod:`cryptography.fernet` and decrypts
transparently on read. The encryption key is a urlsafe-base64 32-byte
Fernet key (``Fernet.generate_key()``).

Concurrency
-----------
A single SQLite connection is opened once and reused (``check_same_thread=
False``) behind an :class:`threading.RLock` so write-heavy callers (one
``add_step`` per agent step) do not reopen the file, replay WAL pragmas,
and pay handshake overhead on every call. Async helpers (``a*``) wrap the
synchronous API with ``asyncio.to_thread`` so awaiting them does not
block the event loop.
"""

from __future__ import annotations

import asyncio
import json
import os
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

_ENCRYPTED_PREFIX = "enc:"


def _load_fernet(key: str | bytes | None) -> Any | None:
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning(
            "baselithbot_replay_encryption_unavailable",
            reason="cryptography not installed; screenshots stored in plaintext",
        )
        return None
    try:
        return Fernet(key if isinstance(key, bytes) else key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        logger.warning("baselithbot_replay_encryption_key_invalid", error=str(exc))
        return None


class TaskReplayStore:
    """SQLite-backed recorder for agent run steps."""

    def __init__(
        self,
        path: str | Path,
        *,
        encryption_key: str | bytes | None = None,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        resolved_key = (
            encryption_key
            or os.environ.get("BASELITHBOT_REPLAY_ENCRYPTION_KEY", "").strip()
            or None
        )
        self._fernet = _load_fernet(resolved_key)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Screenshot encryption helpers
    # ------------------------------------------------------------------
    def _encrypt_screenshot(self, value: str | None) -> str | None:
        if value is None or self._fernet is None:
            return value
        if value.startswith(_ENCRYPTED_PREFIX):
            return value
        token = self._fernet.encrypt(value.encode("utf-8")).decode("ascii")
        return f"{_ENCRYPTED_PREFIX}{token}"

    def _decrypt_screenshot(self, value: str | None) -> str | None:
        if value is None or not value.startswith(_ENCRYPTED_PREFIX):
            return value
        if self._fernet is None:
            # Ciphertext on disk but no key available — refuse to surface it.
            return None
        try:
            token = value[len(_ENCRYPTED_PREFIX) :].encode("ascii")
            return self._fernet.decrypt(token).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - Fernet raises several types
            logger.warning("baselithbot_replay_decrypt_failed", error=str(exc))
            return None

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def start_run(
        self,
        *,
        run_id: str,
        goal: str,
        start_url: str | None,
        max_steps: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs "
                "(run_id, goal, start_url, max_steps, status, started_at) "
                "VALUES (?, ?, ?, ?, 'running', ?)",
                (run_id, goal, start_url, max_steps, time.time()),
            )
            self._conn.commit()

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
        stored_screenshot = self._encrypt_screenshot(screenshot_b64)
        with self._lock:
            self._conn.execute(
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
                    stored_screenshot,
                    json.dumps(extracted_data, default=str),
                ),
            )
            self._conn.commit()

    def finish_run(
        self,
        *,
        run_id: str,
        success: bool,
        final_url: str,
        error: str | None,
        extracted_data: dict[str, Any],
    ) -> None:
        with self._lock:
            self._conn.execute(
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
            self._conn.commit()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def list_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT r.run_id, r.goal, r.start_url, r.max_steps, r.status, r.started_at, "
                "r.completed_at, r.final_url, r.error, "
                "(SELECT COUNT(*) FROM steps s WHERE s.run_id = r.run_id) AS step_count "
                "FROM runs r ORDER BY r.started_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            run_row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run_row is None:
                return None
            step_rows = self._conn.execute(
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
        screenshot_steps = 0
        distinct_urls: set[str] = set()
        for row in step_rows:
            step = dict(row)
            raw_shot = step.get("screenshot_b64")
            step["screenshot_b64"] = self._decrypt_screenshot(raw_shot)
            if raw_shot:
                screenshot_steps += 1
            current_url = step.get("current_url")
            if isinstance(current_url, str) and current_url:
                distinct_urls.add(current_url)
            if step.get("extracted_json"):
                try:
                    step["extracted_data"] = json.loads(step["extracted_json"])
                except json.JSONDecodeError:
                    step["extracted_data"] = {}
            else:
                step["extracted_data"] = {}
            step.pop("extracted_json", None)
            steps.append(step)
        run["step_count"] = len(steps)
        run["screenshot_steps"] = screenshot_steps
        run["first_step_ts"] = steps[0]["ts"] if steps else None
        run["last_step_ts"] = steps[-1]["ts"] if steps else None
        run["distinct_url_count"] = len(distinct_urls)
        run["steps"] = steps
        return run

    # ------------------------------------------------------------------
    # Retention
    # ------------------------------------------------------------------
    def prune_older_than(self, *, retention_seconds: float) -> int:
        """Drop runs (and their steps) whose ``started_at`` is older than cutoff.

        A non-positive ``retention_seconds`` deletes every recorded run.
        """
        cutoff = time.time() - float(retention_seconds)
        with self._lock:
            rows = self._conn.execute(
                "SELECT run_id FROM runs WHERE started_at < ?", (cutoff,)
            ).fetchall()
            run_ids = [row["run_id"] for row in rows]
            if not run_ids:
                return 0
            placeholders = ",".join("?" for _ in run_ids)
            self._conn.execute(
                f"DELETE FROM steps WHERE run_id IN ({placeholders})",  # nosec B608
                run_ids,
            )
            self._conn.execute(
                f"DELETE FROM runs WHERE run_id IN ({placeholders})",  # nosec B608
                run_ids,
            )
            self._conn.commit()
        logger.info("baselithbot_replay_pruned", runs=len(run_ids))
        return len(run_ids)

    # ------------------------------------------------------------------
    # Async helpers (wrap the sync methods via asyncio.to_thread so the
    # event loop is not blocked while SQLite fsyncs — important on the
    # per-agent-step hot path).
    # ------------------------------------------------------------------
    async def astart_run(self, **kwargs: Any) -> None:
        await asyncio.to_thread(self.start_run, **kwargs)

    async def aadd_step(self, **kwargs: Any) -> None:
        await asyncio.to_thread(self.add_step, **kwargs)

    async def afinish_run(self, **kwargs: Any) -> None:
        await asyncio.to_thread(self.finish_run, **kwargs)


__all__ = ["TaskReplayStore"]
