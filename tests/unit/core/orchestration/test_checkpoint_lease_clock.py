"""Checkpoint leases are measured on the database clock, not the replica's.

Before, every replica stamped ``lease_expires_at`` from its own ``time.time()``
and compared it against its own clock when claiming. A replica whose clock ran
ahead by more than a lease length saw every live lease as expired and resumed
runs another worker was executing; one whose clock lagged wrote expiries that
were already in the past for everyone else. The Postgres store now sends
durations and lets the server's ``now()`` anchor them.

The first half pins the wire format against a mocked cursor; the second half
replays the skew scenarios against a real PostgreSQL (``BASELITH_TEST_REAL_DB=1``,
see ``test_checkpoint_store_contract.py``).
"""

from __future__ import annotations

import time
import types
import uuid
from collections.abc import Iterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from baselith.core.orchestration import checkpoint_postgres as pg
from baselith.core.orchestration.checkpoint import (
    Checkpoint,
    CheckpointLeaseHeldError,
)
from baselith.core.orchestration.checkpoint_lease import lease_is_live
from baselith.core.orchestration.checkpoint_postgres import PostgresCheckpointStore
from baselith.core.orchestration.checkpoint_postgres_sql import (
    _CLAIM,
    _LOAD,
    _RENEW,
    _STEP_UPDATE,
    _UPSERT,
)

DB_NOW = "EXTRACT(EPOCH FROM now())"


class _SkewedClock:
    """Stands in for the store module's ``time``: real time plus an offset."""

    def __init__(self) -> None:
        self.offset = 0.0
        self.module = types.SimpleNamespace(time=lambda: time.time() + self.offset)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _SkewedClock:
    skewed = _SkewedClock()
    monkeypatch.setattr(pg, "time", skewed.module)
    return skewed


def _cursor(row: dict[str, Any] | None = None, rowcount: int = 1) -> MagicMock:
    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value=row)
    cur.rowcount = rowcount
    return cur


def _patch_cursor(monkeypatch: pytest.MonkeyPatch, cur: MagicMock) -> None:
    @asynccontextmanager
    async def _ctx(*args: Any, **kwargs: Any) -> Any:
        yield cur

    monkeypatch.setattr(pg, "get_async_cursor", _ctx)


class TestStatementsUseTheDatabaseClock:
    @pytest.mark.parametrize("sql", [_UPSERT, _STEP_UPDATE, _CLAIM, _RENEW, _LOAD])
    def test_every_lease_statement_reads_now_on_the_server(self, sql: str) -> None:
        assert DB_NOW in sql

    def test_claim_compares_expiry_with_the_server_clock(self) -> None:
        assert f"lease_expires_at <= {DB_NOW}" in _CLAIM
        assert "lease_remaining" in _CLAIM and "lease_remaining" in _LOAD


class TestWireFormat:
    async def test_save_sends_the_seconds_left_not_a_replica_timestamp(
        self, monkeypatch: pytest.MonkeyPatch, clock: _SkewedClock
    ) -> None:
        clock.offset = -3600.0  # a replica an hour behind
        cur = _cursor()
        _patch_cursor(monkeypatch, cur)
        cp = Checkpoint(run_id="r", owner="me")
        cp.lease_expires_at = clock.module.time() + 45.0
        await PostgresCheckpointStore().save(cp)

        sent = cur.execute.call_args_list[0].args[1][6]
        assert sent == pytest.approx(45.0, abs=1.0)

    async def test_unleased_save_sends_null(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cur = _cursor()
        _patch_cursor(monkeypatch, cur)
        await PostgresCheckpointStore().save(Checkpoint(run_id="r"))
        assert cur.execute.call_args_list[0].args[1][6] is None

    async def test_step_save_sends_the_seconds_left(
        self, monkeypatch: pytest.MonkeyPatch, clock: _SkewedClock
    ) -> None:
        clock.offset = 900.0
        cur = _cursor()
        _patch_cursor(monkeypatch, cur)
        cp = Checkpoint(run_id="r", owner="me")
        cp.lease_expires_at = clock.module.time() + 20.0
        await PostgresCheckpointStore().save_step(cp, "k", {"x": 1}, {"t": 1})

        params = cur.execute.call_args_list[0].args[1]
        assert params[6] == "me"
        assert params[7] == pytest.approx(20.0, abs=1.0)

    async def test_claim_and_renew_send_durations(
        self, monkeypatch: pytest.MonkeyPatch, clock: _SkewedClock
    ) -> None:
        clock.offset = 5000.0  # the absolute clock value must not matter
        row = {
            "data": {"run_id": "r"},
            "version": 2,
            "owner": "me",
            "lease_remaining": 30.0,
        }
        cur = _cursor(row)
        _patch_cursor(monkeypatch, cur)
        store = PostgresCheckpointStore()

        await store.claim("r", "me", 30.0)
        owner, owner2, length, run_id, owner3, shift = cur.execute.call_args.args[1]
        assert (owner, owner2, run_id, owner3) == ("me", "me", "r", "me")
        assert length == pytest.approx(30.0) and shift == pytest.approx(0.0)

        assert await store.renew_lease("r", "me", 30.0) is True
        assert cur.execute.call_args.args[1] == (pytest.approx(30.0), "r", "me")

    async def test_injected_now_becomes_a_shift_of_the_server_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cur = _cursor({"data": {"run_id": "r"}, "lease_remaining": -30.0})
        _patch_cursor(monkeypatch, cur)
        await PostgresCheckpointStore().claim("r", "me", 30.0, now=time.time() - 60)
        params = cur.execute.call_args.args[1]
        assert params[2] == pytest.approx(-30.0, abs=1.0)
        assert params[5] == pytest.approx(-60.0, abs=1.0)

    async def test_reads_convert_the_remaining_lease_to_the_local_clock(
        self, monkeypatch: pytest.MonkeyPatch, clock: _SkewedClock
    ) -> None:
        clock.offset = -7200.0
        row = {
            "data": {"run_id": "r"},
            "version": 3,
            "owner": "w",
            "lease_remaining": 40.0,
        }
        _patch_cursor(monkeypatch, _cursor(row))
        cp = await PostgresCheckpointStore().load("r")

        assert cp is not None
        local_now = clock.module.time()
        assert cp.lease_expires_at == pytest.approx(local_now + 40.0, abs=1.0)
        # The skewed replica's own liveness check agrees with the server.
        assert lease_is_live(cp, now=local_now)

    async def test_an_unowned_row_reads_back_without_an_expiry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = {
            "data": {"run_id": "r", "lease_expires_at": 123.0},
            "owner": None,
            "lease_remaining": None,
        }
        _patch_cursor(monkeypatch, _cursor(row))
        cp = await PostgresCheckpointStore().load("r")
        assert cp is not None and cp.lease_expires_at is None

    async def test_a_refused_claim_reports_the_holder_on_the_local_clock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cur = _cursor()
        cur.fetchone = AsyncMock(
            side_effect=[None, {"owner": "w", "lease_remaining": 25.0}]
        )
        _patch_cursor(monkeypatch, cur)
        with pytest.raises(CheckpointLeaseHeldError) as held:
            await PostgresCheckpointStore().claim("r", "me", 30.0)
        assert held.value.owner == "w"
        assert held.value.lease_expires_at == pytest.approx(time.time() + 25.0, abs=1.0)


# --- against a real PostgreSQL ------------------------------------------------


@pytest.fixture
def real_store(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[type[PostgresCheckpointStore]]:
    from tests.unit.core.orchestration.test_checkpoint_store_contract import (
        _postgres_backend,
    )

    with _postgres_backend(monkeypatch) as store_cls:
        yield store_cls


async def test_a_replica_running_ahead_cannot_steal_a_live_lease(
    real_store: type[PostgresCheckpointStore], clock: _SkewedClock
) -> None:
    store = real_store()
    run_id = f"skew-{uuid.uuid4().hex[:8]}"
    await store.save(Checkpoint(run_id=run_id, query="q"))
    assert await store.claim(run_id, "worker-a", 30.0) is not None

    clock.offset = 600.0  # ten minutes ahead: by its clock the lease is long gone
    with pytest.raises(CheckpointLeaseHeldError) as held:
        await real_store().claim(run_id, "worker-b", 30.0)
    assert held.value.owner == "worker-a"
    loaded = await real_store().load(run_id)
    assert loaded is not None and lease_is_live(loaded, now=clock.module.time())


async def test_a_lagging_replica_writes_a_lease_that_is_live_for_everyone(
    real_store: type[PostgresCheckpointStore], clock: _SkewedClock
) -> None:
    clock.offset = -600.0  # ten minutes behind
    store = real_store()
    run_id = f"lag-{uuid.uuid4().hex[:8]}"
    cp = Checkpoint(run_id=run_id, query="q", owner="worker-a")
    cp.lease_expires_at = clock.module.time() + 30.0
    await store.save(cp)

    clock.offset = 0.0
    with pytest.raises(CheckpointLeaseHeldError):
        await real_store().claim(run_id, "worker-b", 30.0)


async def test_an_expired_lease_is_still_taken_over(
    real_store: type[PostgresCheckpointStore],
) -> None:
    store = real_store()
    run_id = f"exp-{uuid.uuid4().hex[:8]}"
    await store.save(Checkpoint(run_id=run_id, query="q"))
    await store.claim(run_id, "worker-a", 0.05)
    time.sleep(0.2)
    taken = await real_store().claim(run_id, "worker-b", 30.0)
    assert taken is not None and taken.owner == "worker-b"
    assert taken.lease_expires_at == pytest.approx(time.time() + 30.0, abs=2.0)
