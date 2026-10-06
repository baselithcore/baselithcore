"""``/health/ready`` answers 503 as soon as the process starts draining.

A pod that has received SIGTERM keeps a healthy database, so a readiness probe
that only checked dependencies kept answering 200 and the proxy kept routing
new requests to a server that was closing its listeners.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import Response

from core.lifecycle import drain
from plugins.api_routers import status


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    drain._reset_for_tests()
    status.reset_readiness_cache()
    yield
    drain._reset_for_tests()
    status.reset_readiness_cache()


async def test_draining_answers_503_without_probing(monkeypatch) -> None:
    db = AsyncMock(return_value=True)
    monkeypatch.setattr(status, "_check_database", db)
    drain.mark_draining()

    response = Response()
    body = await status.readiness(response)

    assert response.status_code == 503
    assert body["status"] == "draining"
    db.assert_not_awaited()


async def test_draining_bypasses_a_cached_ready(monkeypatch) -> None:
    ok = AsyncMock(return_value=True)
    monkeypatch.setattr(status, "_check_database", ok)
    monkeypatch.setattr(status, "_check_redis", ok)
    monkeypatch.setattr(status, "_check_vectorstore", ok)

    first = Response()
    assert (await status.readiness(first))["status"] == "ready"
    assert first.status_code == 200

    drain.mark_draining()
    second = Response()
    body = await status.readiness(second)

    assert second.status_code == 503
    assert body["status"] == "draining"
