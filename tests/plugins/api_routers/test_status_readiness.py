"""Tests for the liveness/readiness probes in the status router."""

import pytest

from plugins.api_routers import status


class _FakeResponse:
    """Minimal stand-in for fastapi.Response (only status_code is used)."""

    status_code = 200


@pytest.fixture(autouse=True)
def _fresh_health_cache():
    # Readiness caches results in a process-wide checker; clear between tests.
    status.get_health_checker().invalidate()
    yield
    status.get_health_checker().invalidate()


def test_liveness_is_cheap_and_unconditional():
    assert status.health_check() == {"status": "ok"}


async def test_readiness_ok_when_db_up(monkeypatch):
    monkeypatch.setattr(status, "_check_database", lambda: _async(True))
    monkeypatch.setattr(status, "_check_redis", lambda: _async(True))
    resp = _FakeResponse()
    body = await status.readiness(resp)  # type: ignore[arg-type]
    assert resp.status_code == 200
    assert body["status"] == "ready"
    assert body["services"]["database"] is True


async def test_readiness_503_when_db_down(monkeypatch):
    monkeypatch.setattr(status, "_check_database", lambda: _async(False))
    monkeypatch.setattr(status, "_check_redis", lambda: _async(True))
    resp = _FakeResponse()
    body = await status.readiness(resp)  # type: ignore[arg-type]
    assert resp.status_code == 503
    assert body["status"] == "not_ready"


async def test_readiness_ok_when_only_redis_down(monkeypatch):
    # Redis is advisory: DB up alone must keep the pod ready.
    monkeypatch.setattr(status, "_check_database", lambda: _async(True))
    monkeypatch.setattr(status, "_check_redis", lambda: _async(False))
    resp = _FakeResponse()
    body = await status.readiness(resp)  # type: ignore[arg-type]
    assert resp.status_code == 200
    assert body["services"]["redis"] is False


async def test_db_check_never_raises(monkeypatch):
    # Force the import/connection path to blow up; helper must swallow it.
    def _boom(*_a, **_k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("core.db.connection.get_async_connection", _boom, raising=False)
    assert await status._check_database() is False


async def _async(value):
    return value
