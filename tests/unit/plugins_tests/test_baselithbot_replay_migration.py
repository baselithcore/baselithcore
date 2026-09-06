"""A replay store created before tenancy must still open after the upgrade.

Regression: ``_SCHEMA`` used to create ``idx_runs_tenant`` on ``runs(tenant_id)``
before the ``ALTER TABLE`` that adds the column, so every existing
``replay.sqlite`` aborted ``TaskReplayStore.__init__`` with
``sqlite3.OperationalError: no such column: tenant_id``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from plugins.baselithbot.control.replay import TaskReplayStore

_PRE_TENANCY_SCHEMA = """
CREATE TABLE runs (
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
CREATE TABLE steps (
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
CREATE INDEX idx_runs_started_at ON runs(started_at);
"""


def _pre_tenancy_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(_PRE_TENANCY_SCHEMA)
    conn.execute(
        "INSERT INTO runs (run_id, goal, started_at, status) VALUES (?, ?, ?, ?)",
        ("run-old", "legacy goal", 1.0, "completed"),
    )
    conn.commit()
    conn.close()


def test_pre_tenancy_store_opens_and_gains_tenant_column(tmp_path: Path) -> None:
    db = tmp_path / "replay.sqlite"
    _pre_tenancy_db(db)

    store = TaskReplayStore(db)
    try:
        conn = sqlite3.connect(db)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(runs)")}
        conn.close()
    finally:
        store.close()

    assert "tenant_id" in columns
    assert "idx_runs_tenant" in indexes


def test_legacy_rows_belong_to_the_default_tenant(tmp_path: Path) -> None:
    db = tmp_path / "replay.sqlite"
    _pre_tenancy_db(db)

    store = TaskReplayStore(db)
    try:
        runs = store.list_runs(limit=10)
    finally:
        store.close()

    assert [run["run_id"] for run in runs] == ["run-old"]
