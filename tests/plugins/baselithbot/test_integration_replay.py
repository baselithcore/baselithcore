"""Integration tests for TaskReplayStore (SQLite-backed).

Marked ``@pytest.mark.slow`` because they do real SQLite I/O under a
per-test ``tmp_path``. Skip in fast unit runs, include in nightly CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plugins.baselithbot.control.replay import TaskReplayStore

pytestmark = pytest.mark.slow


@pytest.fixture
def store(tmp_path: Path) -> TaskReplayStore:
    return TaskReplayStore(tmp_path / "replay.sqlite")


class TestReplayStorePersistence:
    def test_record_and_fetch_run(self, store: TaskReplayStore) -> None:
        run_id = "run-123"
        store.start_run(
            run_id=run_id,
            goal="collect prices",
            start_url="https://example.com",
            max_steps=10,
        )
        store.add_step(
            run_id=run_id,
            step_index=0,
            action="navigate",
            reasoning="entry point",
            current_url="https://example.com",
            screenshot_b64=None,
            extracted_data={},
        )
        store.finish_run(
            run_id=run_id,
            success=True,
            final_url="https://example.com/cart",
            error=None,
            extracted_data={"total": "9.99"},
        )

        fetched = store.get_run(run_id)
        assert fetched is not None
        assert fetched["goal"] == "collect prices"
        assert fetched["status"] == "completed"
        assert len(fetched["steps"]) == 1
        assert fetched["steps"][0]["action"] == "navigate"

    def test_list_runs_orders_by_started_desc(self, store: TaskReplayStore) -> None:
        import time

        for idx in range(3):
            run_id = f"run-{idx}"
            store.start_run(
                run_id=run_id, goal=f"goal-{idx}", start_url=None, max_steps=1
            )
            store.finish_run(
                run_id=run_id,
                success=True,
                final_url="",
                error=None,
                extracted_data={},
            )
            time.sleep(0.01)

        runs = store.list_runs(limit=10)
        assert len(runs) == 3
        assert runs[0]["run_id"] == "run-2"
        assert runs[-1]["run_id"] == "run-0"

    def test_prune_older_than_drops_expired_runs(self, store: TaskReplayStore) -> None:
        store.start_run(run_id="fresh", goal="g", start_url=None, max_steps=1)
        store.finish_run(
            run_id="fresh",
            success=True,
            final_url="",
            error=None,
            extracted_data={},
        )

        # retention of 1 hour keeps the just-recorded run
        pruned = store.prune_older_than(retention_seconds=3600.0)
        assert pruned == 0
        assert store.get_run("fresh") is not None

        # retention of 0 seconds drops everything
        pruned_all = store.prune_older_than(retention_seconds=0.0)
        assert pruned_all == 1
        assert store.get_run("fresh") is None
