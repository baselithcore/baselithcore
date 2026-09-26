"""Tests for the async agent-run API (/agent/async, /agent/status)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plugins.api_routers.async_runs as async_runs_module
from core.middleware import require_user
from plugins.api_routers.async_runs import router

pytestmark = [pytest.mark.unit]


class _Tracker:
    def __init__(self) -> None:
        self.statuses: dict[str, dict] = {}

    def get_status(self, task_id):
        return self.statuses.get(task_id)

    def get_status_for_tenant(self, task_id, tenant_id):
        status = self.get_status(task_id)
        if status is None or status.get("tenant_id") != tenant_id:
            return None
        return status


@pytest.fixture
def tracker():
    return _Tracker()


@pytest.fixture
def client(tracker, monkeypatch):
    monkeypatch.setattr(async_runs_module, "_enqueue", lambda query, cid: "job-123")
    monkeypatch.setattr(async_runs_module, "_tracker", lambda: tracker)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_user] = lambda: "user"
    return TestClient(app)


class TestSubmit:
    def test_submit_returns_task_id(self, client):
        resp = client.post("/agent/async", json={"query": "do the thing"})
        assert resp.status_code == 202
        body = resp.json()
        assert body["task_id"] == "job-123"
        assert body["status_url"].endswith("/agent/status/job-123")

    def test_empty_query_rejected(self, client):
        resp = client.post("/agent/async", json={"query": ""})
        assert resp.status_code == 422

    def test_queue_unavailable_returns_503(self, tracker, monkeypatch):
        def broken(query, cid):
            raise ConnectionError("redis down")

        monkeypatch.setattr(async_runs_module, "_enqueue", broken)
        monkeypatch.setattr(async_runs_module, "_tracker", lambda: tracker)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[require_user] = lambda: "user"
        resp = TestClient(app).post("/agent/async", json={"query": "x"})
        assert resp.status_code == 503


class TestStatus:
    def test_status_found(self, client, tracker):
        tracker.statuses["job-123"] = {
            "tenant_id": "default",
            "status": "completed",
            "result": {"answer": "ok"},
        }
        resp = client.get("/agent/status/job-123")
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_status_unknown_404(self, client):
        assert client.get("/agent/status/nope").status_code == 404

    def test_status_of_another_tenant_is_404(self, client, tracker):
        tracker.statuses["job-123"] = {"tenant_id": "tenant-b", "status": "completed"}
        assert client.get("/agent/status/job-123").status_code == 404

    def test_status_uses_request_tenant(self, client, tracker, monkeypatch):
        tracker.statuses["job-123"] = {"tenant_id": "tenant-b", "status": "running"}
        monkeypatch.setattr(
            async_runs_module, "get_current_tenant_id", lambda: "tenant-b"
        )
        resp = client.get("/agent/status/job-123")
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_ownerless_record_is_404(self, client, tracker):
        tracker.statuses["job-123"] = {"status": "completed"}
        assert client.get("/agent/status/job-123").status_code == 404

    def test_tracker_unavailable_returns_503(self, client, monkeypatch):
        def broken():
            raise ConnectionError("redis down")

        monkeypatch.setattr(async_runs_module, "_tracker", broken)
        assert client.get("/agent/status/job-123").status_code == 503
