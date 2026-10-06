"""``POST /reindex`` and ``POST /index/bootstrap`` answer 202 + a poll URL.

``/reindex`` used to run the whole incremental indexing pass inside the
request: a large corpus held the worker slot for minutes and a proxy timeout
turned work that was still running into a client-side failure.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plugins.api_routers.index as index_module
from core.middleware import require_admin_or_job
from core.services.bootstrap import IndexBootstrapper


@pytest.fixture
def bootstrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IndexBootstrapper:
    instance = IndexBootstrapper(
        enabled=True,
        sentinel_path=tmp_path / "sentinel",
        state_path=tmp_path / "state.json",
    )
    monkeypatch.setattr(index_module, "bootstrapper", instance)
    return instance


@pytest.fixture
def client(bootstrapper: IndexBootstrapper) -> Any:
    app = FastAPI()
    app.include_router(index_module.router)
    app.dependency_overrides[require_admin_or_job] = lambda: "admin"
    with TestClient(app) as test_client:
        yield test_client


class TestReindex:
    def test_returns_202_with_status_url_and_location(
        self, client: Any, bootstrapper: IndexBootstrapper, monkeypatch: Any
    ) -> None:
        started: list[str] = []

        async def fake_schedule_manual(mode: str = "incremental") -> bool:
            started.append(mode)
            return True

        monkeypatch.setattr(bootstrapper, "schedule_manual", fake_schedule_manual)
        resp = client.post("/reindex")
        assert resp.status_code == 202
        assert resp.json() == {
            "status": "scheduled",
            "mode": "incremental",
            "status_url": "/v1/index/status",
        }
        assert resp.headers["location"] == "/v1/index/status"
        assert started == ["incremental"]

    def test_409_while_a_run_is_in_progress(
        self, client: Any, bootstrapper: IndexBootstrapper, monkeypatch: Any
    ) -> None:
        async def busy(mode: str = "incremental") -> bool:
            return False

        monkeypatch.setattr(bootstrapper, "schedule_manual", busy)
        assert client.post("/reindex").status_code == 409


class TestBootstrap:
    def test_returns_202_with_status_url(
        self, client: Any, bootstrapper: IndexBootstrapper, monkeypatch: Any
    ) -> None:
        monkeypatch.setattr(index_module, "INDEX_BOOTSTRAP_ENABLED", True)

        async def scheduled(**_: Any) -> bool:
            bootstrapper._current_mode = "full"
            return True

        monkeypatch.setattr(bootstrapper, "schedule", scheduled)
        resp = client.post("/index/bootstrap")
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "scheduled"
        assert body["mode"] == "full"
        assert body["status_url"] == "/v1/index/status"
        assert resp.headers["location"] == "/v1/index/status"

    def test_503_when_disabled(self, client: Any, monkeypatch: Any) -> None:
        monkeypatch.setattr(index_module, "INDEX_BOOTSTRAP_ENABLED", False)
        assert client.post("/index/bootstrap").status_code == 503


class TestScheduleManual:
    @pytest.mark.asyncio
    async def test_runs_in_background_and_reports_through_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import core.services.bootstrap as bootstrap_module

        release = asyncio.Event()

        class _Indexing:
            async def index_documents(self, incremental: bool = True) -> Any:
                await release.wait()
                return SimpleNamespace(new_documents=7)

        monkeypatch.setattr(bootstrap_module, "get_indexing_service", _Indexing)
        # Disabled bootstrap must not block an operator-requested run.
        instance = IndexBootstrapper(
            enabled=False,
            sentinel_path=tmp_path / "s",
            state_path=tmp_path / "st",
        )
        monkeypatch.setattr(
            "core.graph.graph_db", SimpleNamespace(is_enabled=lambda: False)
        )
        assert await instance.schedule_manual("incremental") is True
        assert instance.status()["running"] is True
        # The task slot is shared: no overlapping second run.
        assert await instance.schedule_manual("incremental") is False
        release.set()
        for _ in range(100):
            if not instance.is_running():
                break
            await asyncio.sleep(0.01)
        status = instance.status()
        assert status["running"] is False
        assert status["last_completed"] == "incremental"
        assert status["last_new_documents"] == 7
