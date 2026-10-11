"""One persistence contract, three backends: memory, SQLite, PostgreSQL.

Each backend used to persist a different subset of the run on a step save:
Postgres and memory patched only the step bookkeeping and dropped
``plugin_data`` and the budget, SQLite rewrote everything. A crash therefore
recovered a different run depending on the backend. These cases drive the
three stores through the same steps, "crash" (drop every in-process object and
read the run back — a fresh store instance on the same file/database for the
durable backends), and require the identical recovered state.

The same parametrization pins the concurrency contract: compare-and-swap on
``version`` and the per-run lease.

The Postgres case runs only against a real server, with the real-database
opt-in (see ``tests/integration/test_tool_ledger_postgres.py``)::

    BASELITH_TEST_REAL_DB=1 python -m pytest tests/unit/core/orchestration/test_checkpoint_store_contract.py

It builds the pre-upgrade table in a throwaway schema and applies migration
013 to it, so the migration and the store are tested together. The CI
``integration_test`` job runs it with a Postgres service and
``BASELITH_TEST_INTEGRATION_STRICT=1``, under which a missing database fails
the case instead of skipping it (this module sits outside
``tests/integration/``, whose conftest does the same for its own files).
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from baselith.core.orchestration.checkpoint import (
    STATUS_RUNNING,
    Checkpoint,
    CheckpointConflictError,
    CheckpointLeaseHeldError,
    CheckpointManager,
    InMemoryCheckpointStore,
)
from baselith.core.orchestration.checkpoint_sqlite import SQLiteCheckpointStore
from baselith.core.orchestration.limits import LoopBudget

pytestmark = [pytest.mark.unit, pytest.mark.contract]

MIGRATION_013 = (
    Path(__file__).resolve().parents[4]
    / "baselith"
    / "core"
    / "db"
    / "migrations"
    / "versions"
    / "014_checkpoint_leases.py"
)

#: The table exactly as migration 007 created it, before the lease columns.
_PRE_013_TABLE = """
CREATE TABLE agent_checkpoints (
    run_id      TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    status      TEXT NOT NULL DEFAULT 'running',
    data        JSONB NOT NULL,
    version     INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
)
"""


class _Op:
    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def execute(self, sql: str) -> None:
        self._cur.execute(sql)


def _skip_unless_strict(reason: str) -> None:
    """Skip — or fail, in the integration job, where Postgres must be there."""
    strict = os.environ.get("BASELITH_TEST_INTEGRATION_STRICT", "").strip().lower()
    if strict in {"1", "true", "yes"}:
        pytest.fail(f"skipped under BASELITH_TEST_INTEGRATION_STRICT=1: {reason}")
    pytest.skip(reason)


@contextlib.contextmanager
def _postgres_backend(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[], Any]]:
    from tests.integration.test_tool_ledger_postgres import (
        _conninfo,
        _pg_available,
        _real_db_enabled,
    )

    if not _real_db_enabled():
        _skip_unless_strict(
            "set BASELITH_TEST_REAL_DB=1 to run against a real Postgres"
        )
    if not _pg_available():
        _skip_unless_strict("PostgreSQL not reachable (docker compose up -d postgres)")

    import psycopg

    schema = f"itest_ckpt_{uuid.uuid4().hex[:8]}"
    spec = importlib.util.spec_from_file_location("mig_013", MIGRATION_013)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {schema}")
        cur.execute(f"SET search_path TO {schema}")
        cur.execute(_PRE_013_TABLE)
        migration.op = _Op(cur)
        migration.upgrade()

    @contextlib.asynccontextmanager
    async def _cursor(*, row_factory: Any = None) -> AsyncIterator[Any]:
        conn = await psycopg.AsyncConnection.connect(_conninfo(), autocommit=True)
        try:
            await conn.execute(f"SET search_path TO {schema}")
            kwargs = {"row_factory": row_factory} if row_factory is not None else {}
            async with conn.cursor(**kwargs) as cur:
                yield cur
        finally:
            await conn.close()

    from baselith.core.orchestration.checkpoint_postgres import (
        PostgresCheckpointStore,
    )

    monkeypatch.setattr(
        "baselith.core.orchestration.checkpoint_postgres.get_async_cursor", _cursor
    )
    try:
        yield PostgresCheckpointStore
    finally:
        with (
            psycopg.connect(_conninfo(), autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def open_store(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[], Any]]:
    """A factory for the backend under test; each call is a "new process".

    Memory has no second process to survive into, so its "new instance" is
    the same object — what it must still get right is persisting the full
    state on every step save.
    """
    if request.param == "memory":
        shared = InMemoryCheckpointStore()
        yield lambda: shared
    elif request.param == "sqlite":
        path = tmp_path / "checkpoints.db"
        opened: list[SQLiteCheckpointStore] = []

        def _open() -> SQLiteCheckpointStore:
            opened.append(SQLiteCheckpointStore(path))
            return opened[-1]

        yield _open
        for store in opened:
            store.close()
    else:
        with _postgres_backend(monkeypatch) as store_cls:
            yield store_cls


def _normalized(checkpoint: Checkpoint) -> dict[str, Any]:
    state = checkpoint.to_dict()
    # Wall-clock fields differ by construction; ``at`` is per step.
    for key in ("created_at", "updated_at"):
        state.pop(key)
    for entry in state["steps"].values():
        entry.pop("at", None)
    return state


async def _drive_a_run(store: Any, run_id: str) -> dict[str, Any]:
    """Three steps that also move budget, plugin data and inputs."""
    budget = LoopBudget()
    checkpoint = Checkpoint(
        run_id=run_id,
        tenant_id="acme",
        query="q",
        inputs={"strategy": "react", "history_text": "earlier turn"},
    )
    await store.save(checkpoint)
    manager = CheckpointManager(store, checkpoint, budget=budget)
    checkpoint.intent = "complex_reasoning"
    for n in range(3):
        budget.tick()
        budget.record_tool_call()
        budget.record_tokens(100)
        checkpoint.plugin_data[f"scratch_{n}"] = {"seen": n}

        async def _tool(n: int = n) -> dict[str, int]:
            return {"value": n}

        await manager.run_step("lookup", {"n": n}, _tool)
    return _normalized(checkpoint)


async def test_a_step_save_persists_everything_a_crash_must_recover(open_store):
    run_id = f"contract-{uuid.uuid4().hex[:8]}"
    expected = await _drive_a_run(open_store(), run_id)

    recovered = await open_store().load(run_id)  # the "restarted process"

    assert recovered is not None
    assert _normalized(recovered) == expected
    # The fields the old step save dropped, spelled out.
    assert recovered.plugin_data == {f"scratch_{n}": {"seen": n} for n in range(3)}
    assert recovered.budget["iterations"] == 3
    assert recovered.budget["tool_calls"] == 3
    assert recovered.budget["tokens"] == 300
    assert recovered.intent == "complex_reasoning"
    assert recovered.inputs == {"strategy": "react", "history_text": "earlier turn"}
    assert recovered.step == 3 and len(recovered.trajectory) == 3


async def test_a_stale_copy_cannot_overwrite_newer_state(open_store):
    store = open_store()
    run_id = f"cas-{uuid.uuid4().hex[:8]}"
    await store.save(Checkpoint(run_id=run_id, query="q"))
    mine = await store.load(run_id)
    theirs = await store.load(run_id)
    assert mine is not None and theirs is not None

    theirs.plugin_data["winner"] = "theirs"
    await store.save(theirs)

    mine.plugin_data["winner"] = "mine"
    with pytest.raises(CheckpointConflictError):
        await store.save(mine)

    async def _tool() -> str:
        return "side effect"

    with pytest.raises(CheckpointConflictError):
        await CheckpointManager(store, mine).run_step("t", {}, _tool)

    stored = await open_store().load(run_id)
    assert stored is not None and stored.plugin_data == {"winner": "theirs"}
    assert stored.steps == {}


async def test_a_fresh_copy_of_an_existing_run_is_refused(open_store):
    store = open_store()
    run_id = f"fresh-{uuid.uuid4().hex[:8]}"
    await store.save(Checkpoint(run_id=run_id, query="first"))
    with pytest.raises(CheckpointConflictError):
        await store.save(Checkpoint(run_id=run_id, query="second"))
    stored = await store.load(run_id)
    assert stored is not None and stored.query == "first"


async def test_the_lease_admits_one_owner_at_a_time(open_store):
    store = open_store()
    run_id = f"lease-{uuid.uuid4().hex[:8]}"
    assert await store.claim("missing-run", "a", 30.0) is None
    await store.save(Checkpoint(run_id=run_id, query="q"))

    claimed = await store.claim(run_id, "worker-a", 30.0)
    assert claimed is not None and claimed.owner == "worker-a"
    with pytest.raises(CheckpointLeaseHeldError) as held:
        await open_store().claim(run_id, "worker-b", 30.0)
    assert held.value.owner == "worker-a"

    # Re-claiming one's own lease is a renewal, not a takeover.
    again = await store.claim(run_id, "worker-a", 30.0)
    assert again is not None and again.version == claimed.version

    assert await store.renew_lease(run_id, "worker-b", 30.0) is False
    assert await store.renew_lease(run_id, "worker-a", 30.0) is True

    await store.release_lease(run_id, "worker-b")  # not theirs: no effect
    assert (await store.load(run_id)).owner == "worker-a"
    await store.release_lease(run_id, "worker-a")
    released = await store.load(run_id)
    assert released.owner is None and released.lease_expires_at is None
    assert (await store.claim(run_id, "worker-b", 30.0)).owner == "worker-b"


async def test_an_expired_lease_is_taken_over_and_fences_the_old_owner(open_store):
    store = open_store()
    run_id = f"fence-{uuid.uuid4().hex[:8]}"
    await store.save(Checkpoint(run_id=run_id, query="q"))
    old = await store.claim(run_id, "worker-a", 30.0, now=time.time() - 60)
    assert old is not None

    new = await open_store().claim(run_id, "worker-b", 30.0)
    assert new is not None and new.owner == "worker-b"
    assert new.version == old.version + 1

    # The previous owner wakes up and tries to record a step: refused.
    old.plugin_data["late"] = True
    with pytest.raises(CheckpointConflictError):
        await store.save(old)
    assert await store.renew_lease(run_id, "worker-a", 30.0) is False

    stored = await store.load(run_id)
    assert stored is not None
    assert stored.owner == "worker-b" and "late" not in stored.plugin_data
    assert stored.status == STATUS_RUNNING
