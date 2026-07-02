"""Tests for the dead-letter-queue admin endpoints."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.task_queue.dead_letter import DeadLetterError, DeadLetterRecord
from plugins.api_routers import admin


def _record(job_id="j1"):
    return DeadLetterRecord(
        job_id=job_id,
        func_name="m.f",
        origin_queue="documents",
        error="boom",
        traceback="trace",
        failed_at=123.0,
        tenant_id="acme",
        args_repr="(1,)",
        kwargs_repr="{}",
        payload_b64="",
    )


class FakeDLQ:
    def __init__(self):
        self.records = {"j1": _record("j1")}
        self.purged = []
        self.replayed = []

    def list(self, limit=50, offset=0):
        return list(self.records.values())[offset : offset + limit]

    def count(self):
        return len(self.records)

    def get(self, job_id):
        return self.records.get(job_id)

    def replay(self, job_id, purge=True):
        if job_id not in self.records:
            raise DeadLetterError("missing")
        self.replayed.append(job_id)
        return "new-" + job_id

    def purge(self, job_id):
        return self.records.pop(job_id, None) is not None

    def purge_all(self):
        n = len(self.records)
        self.records.clear()
        return n


@pytest.fixture
def client(monkeypatch):
    fake = FakeDLQ()
    monkeypatch.setattr(admin, "get_dead_letter_queue", lambda: fake, raising=False)
    monkeypatch.setattr(
        "core.task_queue.dead_letter.get_dead_letter_queue", lambda: fake
    )
    app = FastAPI()
    app.include_router(admin.router)
    # Bypass Basic Auth.
    app.dependency_overrides[admin.verify_credentials] = lambda: "admin"
    return TestClient(app), fake


def test_list(client):
    c, _ = client
    r = c.get("/admin/dlq")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["job_id"] == "j1"
    assert "traceback" not in body["items"][0]  # summary omits traceback


def test_get_detail(client):
    c, _ = client
    r = c.get("/admin/dlq/j1")
    assert r.status_code == 200
    assert r.json()["traceback"] == "trace"


def test_get_missing_404(client):
    c, _ = client
    assert c.get("/admin/dlq/ghost").status_code == 404


def test_replay(client):
    c, fake = client
    r = c.post("/admin/dlq/j1/replay")
    assert r.status_code == 200
    assert r.json()["job_id"] == "new-j1"
    assert fake.replayed == ["j1"]


def test_replay_conflict(client):
    c, _ = client
    r = c.post("/admin/dlq/ghost/replay")
    assert r.status_code == 409


def test_purge(client):
    c, fake = client
    r = c.delete("/admin/dlq/j1")
    assert r.status_code == 200
    assert "j1" not in fake.records


def test_purge_missing_404(client):
    c, _ = client
    assert c.delete("/admin/dlq/ghost").status_code == 404


def test_purge_all(client):
    c, fake = client
    r = c.delete("/admin/dlq")
    assert r.status_code == 200
    assert r.json()["removed"] == 1
    assert fake.count() == 0
