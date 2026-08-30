"""Tests for the SQLite checkpoint store (durable dev/air-gapped backend)."""

import pytest

from core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    STATUS_COMPLETED,
    STATUS_RUNNING,
    Checkpoint,
)
from core.orchestration.checkpoint_sqlite import SQLiteCheckpointStore

pytestmark = [pytest.mark.unit]


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "checkpoints.db"


@pytest.fixture
def store(db_path):
    return SQLiteCheckpointStore(db_path)


class TestSaveLoad:
    async def test_roundtrip(self, store):
        cp = Checkpoint(run_id="r1", tenant_id="t1", query="q", intent="chat")
        cp.trajectory.append({"tool": "search", "ok": True})
        cp.steps["0:search:abc"] = {"tool_name": "search", "result": 1}
        await store.save(cp)

        loaded = await store.load("r1")
        assert loaded is not None
        assert loaded.run_id == "r1"
        assert loaded.tenant_id == "t1"
        assert loaded.query == "q"
        assert loaded.trajectory == [{"tool": "search", "ok": True}]
        assert loaded.steps == {"0:search:abc": {"tool_name": "search", "result": 1}}

    async def test_save_bumps_version_and_updated_at(self, store):
        cp = Checkpoint(run_id="r1")
        before = cp.updated_at
        await store.save(cp)
        assert cp.version == 1
        assert cp.updated_at >= before
        await store.save(cp)
        assert cp.version == 2

    async def test_load_missing_returns_none(self, store):
        assert await store.load("nope") is None

    async def test_delete(self, store):
        await store.save(Checkpoint(run_id="r1"))
        await store.delete("r1")
        assert await store.load("r1") is None

    async def test_survives_reopen(self, db_path):
        """The whole point of the backend: state outlives the process."""
        first = SQLiteCheckpointStore(db_path)
        await first.save(Checkpoint(run_id="r1", query="persisted"))
        first.close()

        reopened = SQLiteCheckpointStore(db_path)
        loaded = await reopened.load("r1")
        assert loaded is not None
        assert loaded.query == "persisted"
        reopened.close()


class TestSaveStep:
    async def test_save_step_persists_step_and_trajectory(self, store):
        cp = Checkpoint(run_id="r1")
        await store.save(cp)

        cp.step = 1
        cp.steps["1:tool:xyz"] = {"tool_name": "tool", "result": "ok"}
        cp.trajectory.append({"cursor": 1})
        await store.save_step(
            cp, "1:tool:xyz", {"tool_name": "tool", "result": "ok"}, {"cursor": 1}
        )

        loaded = await store.load("r1")
        assert loaded is not None
        assert loaded.step == 1
        assert loaded.steps["1:tool:xyz"]["result"] == "ok"
        assert loaded.trajectory == [{"cursor": 1}]

    async def test_save_step_unsaved_run_falls_back_to_save(self, store):
        cp = Checkpoint(run_id="fresh")
        await store.save_step(cp, "0:t:k", {"tool_name": "t"}, {"cursor": 0})
        assert await store.load("fresh") is not None


class TestListing:
    async def test_list_resumable_filters_status_and_tenant(self, store):
        await store.save(Checkpoint(run_id="run", status=STATUS_RUNNING))
        await store.save(
            Checkpoint(run_id="wait", status=STATUS_AWAITING_APPROVAL, tenant_id="t2")
        )
        await store.save(Checkpoint(run_id="done", status=STATUS_COMPLETED))

        assert set(await store.list_resumable()) == {"run", "wait"}
        assert await store.list_resumable("t2") == ["wait"]

    async def test_list_runs_newest_first_with_filters(self, store):
        await store.save(Checkpoint(run_id="a", status=STATUS_COMPLETED))
        await store.save(Checkpoint(run_id="b", status=STATUS_RUNNING))

        rows = await store.list_runs()
        assert [r["run_id"] for r in rows] == ["b", "a"]
        only_done = await store.list_runs(status=STATUS_COMPLETED)
        assert [r["run_id"] for r in only_done] == ["a"]


class TestHistory:
    async def test_snapshots_recorded_and_loadable(self, db_path):
        store = SQLiteCheckpointStore(db_path, history_enabled=True)
        cp = Checkpoint(run_id="r1", query="v1")
        await store.save(cp)
        cp.query = "v2"
        await store.save(cp)

        snaps = await store.list_snapshots("r1")
        assert [s["version"] for s in snaps] == [1, 2]

        v1 = await store.load_snapshot("r1", 1)
        assert v1 is not None and v1.query == "v1"
        assert await store.load_snapshot("r1", 99) is None
        store.close()

    async def test_history_limit_trims_oldest(self, db_path):
        store = SQLiteCheckpointStore(db_path, history_enabled=True, history_limit=2)
        cp = Checkpoint(run_id="r1")
        for _ in range(4):
            await store.save(cp)
        snaps = await store.list_snapshots("r1")
        assert [s["version"] for s in snaps] == [3, 4]
        store.close()


class TestFactoryResolution:
    async def test_factory_resolves_sqlite_backend(self, tmp_path, monkeypatch):
        from core.config import orchestration as orch_config
        from core.orchestration import checkpoint_factory

        monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "sqlite")
        monkeypatch.setenv(
            "ORCHESTRATOR_CHECKPOINT_SQLITE_PATH", str(tmp_path / "cp.db")
        )
        orch_config._orchestration_config = None
        checkpoint_factory.reset_default_checkpoint_store()
        try:
            resolved = checkpoint_factory.get_default_checkpoint_store()
            assert isinstance(resolved, SQLiteCheckpointStore)
        finally:
            orch_config._orchestration_config = None
            checkpoint_factory.reset_default_checkpoint_store()
