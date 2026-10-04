"""``/health/ready`` answers within its probe deadline when a dependency hangs.

With PostgreSQL unreachable the pool checkout waited the full
``DB_POOL_TIMEOUT`` (30 s) and Kubernetes timed the probe out instead of
reading a 503. Each probe is now bounded, and the outcome — failure included —
is cached and shared by concurrent callers.

The hanging probes below swallow cancellation on purpose: psycopg does the
same (it sends a server-side cancel that itself waits on the dead server), so
a deadline that relies on cancelling the probe still waits the full timeout.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import Response

from plugins.api_routers import status


@pytest.fixture(autouse=True)
def _fast_deadline(monkeypatch):
    cfg = status.get_app_config()
    monkeypatch.setattr(cfg, "health_ready_probe_timeout", 0.2)
    monkeypatch.setattr(cfg, "health_ready_cache_ttl", 5)
    status.reset_readiness_cache()
    yield
    status.reset_readiness_cache()


def _hanging(calls: list[str], name: str):
    async def probe() -> bool:
        calls.append(name)
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(30)  # like psycopg's cancel round-trip
        return True

    return probe


async def _ok() -> bool:
    return True


async def test_unreachable_db_answers_503_within_deadline(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(status, "_check_database", _hanging(calls, "db"))
    monkeypatch.setattr(status, "_check_redis", _hanging(calls, "redis"))
    monkeypatch.setattr(status, "_check_vectorstore", _hanging(calls, "vec"))

    response = Response()
    started = time.perf_counter()
    body = await status.readiness(response)
    elapsed = time.perf_counter() - started

    assert response.status_code == 503
    assert body["status"] == "not_ready"
    assert body["services"] == {
        "database": False,
        "redis": False,
        "vectorstore": False,
    }
    assert elapsed < 2.0


async def test_failure_is_cached_and_concurrent_probes_share_one_check(
    monkeypatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(status, "_check_database", _hanging(calls, "db"))
    monkeypatch.setattr(status, "_check_redis", _ok)
    monkeypatch.setattr(status, "_check_vectorstore", _ok)

    responses = [Response() for _ in range(5)]
    started = time.perf_counter()
    bodies = await asyncio.gather(*(status.readiness(r) for r in responses))
    elapsed = time.perf_counter() - started

    assert calls == ["db"]  # one probe, not five
    assert elapsed < 2.0
    assert all(r.status_code == 503 for r in responses)
    assert sum(1 for b in bodies if b["cached"]) == 4

    again = Response()
    body = await status.readiness(again)
    assert again.status_code == 503
    assert body["cached"] is True
    assert calls == ["db"]


async def test_a_still_running_probe_is_awaited_not_restarted(monkeypatch) -> None:
    # TTL 0: every call refreshes. A probe still stuck from the previous
    # refresh must be waited on again, not joined by a second stuck probe.
    monkeypatch.setattr(status.get_app_config(), "health_ready_cache_ttl", 0)
    status.reset_readiness_cache()
    calls: list[str] = []
    monkeypatch.setattr(status, "_check_database", _hanging(calls, "db"))
    monkeypatch.setattr(status, "_check_redis", _ok)
    monkeypatch.setattr(status, "_check_vectorstore", _ok)

    for _ in range(3):
        response = Response()
        await status.readiness(response)
        assert response.status_code == 503
    assert calls == ["db"]
