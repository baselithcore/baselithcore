"""A PostgreSQL that is down costs boot one short probe, not a pool timeout each."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.db import reachability


@pytest.fixture(autouse=True)
def _reset_probe():
    reachability.reset_postgres_probe()
    yield
    reachability.reset_postgres_probe()


def _storage(enabled: bool = True) -> SimpleNamespace:
    # Port 1 on loopback: nothing listens there, the connect is refused.
    return SimpleNamespace(
        postgres_enabled=enabled,
        conninfo="host=127.0.0.1 port=1 dbname=x user=x connect_timeout=1",
    )


async def test_probe_against_a_closed_port_is_fast_and_recorded() -> None:
    with patch("core.config.get_storage_config", return_value=_storage()):
        started = time.monotonic()
        assert await reachability.probe_postgres(timeout=2.0) is False
    assert time.monotonic() - started < 3.0
    assert reachability.last_postgres_probe() is False
    assert reachability.postgres_known_unreachable() is True


async def test_probe_is_bounded_when_connect_hangs() -> None:
    async def hang(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(60)

    with (
        patch("core.config.get_storage_config", return_value=_storage()),
        patch("psycopg.AsyncConnection.connect", hang),
    ):
        started = time.monotonic()
        assert await reachability.probe_postgres(timeout=0.1) is False
    assert time.monotonic() - started < 1.0


async def test_probe_records_success() -> None:
    conn = SimpleNamespace(execute=AsyncMock(), close=AsyncMock())
    with (
        patch("core.config.get_storage_config", return_value=_storage()),
        patch("psycopg.AsyncConnection.connect", AsyncMock(return_value=conn)),
    ):
        assert await reachability.probe_postgres() is True
    conn.close.assert_awaited_once()
    assert reachability.last_postgres_probe() is True
    assert reachability.postgres_known_unreachable() is False


async def test_disabled_postgres_records_nothing() -> None:
    with patch("core.config.get_storage_config", return_value=_storage(False)):
        assert await reachability.probe_postgres() is False
    assert reachability.last_postgres_probe() is None


async def test_core_schema_init_skips_alembic_when_the_probe_saw_the_db_down() -> None:
    from core.db import schema

    reachability._last_outcome = False
    init_db = AsyncMock()
    with patch.object(schema, "init_db", init_db):
        assert await schema.init_core_schema_best_effort() is False
    init_db.assert_not_awaited()


async def test_core_schema_init_runs_when_the_db_answered() -> None:
    from core.db import schema

    reachability._last_outcome = True
    init_db = AsyncMock()
    with patch.object(schema, "init_db", init_db):
        assert await schema.init_core_schema_best_effort() is True
    init_db.assert_awaited_once()
