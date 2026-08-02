"""Tests for the human-in-the-loop /approvals API."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plugins.api_routers.approvals as approvals_module
from core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    Checkpoint,
    InMemoryCheckpointStore,
)
from plugins.api_routers.admin import verify_credentials
from plugins.api_routers.approvals import router


@pytest.fixture
def store():
    return InMemoryCheckpointStore()


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(approvals_module, "get_default_checkpoint_store", lambda: store)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_credentials] = lambda: "admin"
    return TestClient(app)


async def _paused_run(store, run_id="run-1", tenant=None):
    checkpoint = Checkpoint(run_id=run_id, tenant_id=tenant, query="wipe the table")
    checkpoint.status = STATUS_AWAITING_APPROVAL
    checkpoint.pending_approval = {"tool": "wipe", "category": "destructive"}
    await store.save(checkpoint)
    return checkpoint


class TestListPending:
    def test_lists_awaiting_runs(self, client, store):
        # TestClient runs the app in its own loop; seed synchronously via run.
        import anyio

        anyio.run(lambda: _paused_run(store))
        resp = client.get("/approvals")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        entry = body["pending"][0]
        assert entry["run_id"] == "run-1"
        assert entry["pending_approval"]["tool"] == "wipe"

    def test_empty_when_no_paused_runs(self, client):
        resp = client.get("/approvals")
        assert resp.status_code == 200
        assert resp.json() == {"pending": [], "count": 0}

    def test_503_when_checkpointing_disabled(self, monkeypatch):
        monkeypatch.setattr(
            approvals_module, "get_default_checkpoint_store", lambda: None
        )
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[verify_credentials] = lambda: "admin"
        resp = TestClient(app).get("/approvals")
        assert resp.status_code == 503


class TestDecision:
    def test_records_decision(self, client, store):
        import anyio

        anyio.run(lambda: _paused_run(store))
        resp = client.post(
            "/approvals/run-1/decision",
            json={"approved": True, "approver": "gippo", "reason": "ok"},
        )
        assert resp.status_code == 200
        assert resp.json()["recorded"] is True

        loaded = anyio.run(store.load, "run-1")
        assert loaded.pending_approval["decision"]["approved"] is True
        assert loaded.pending_approval["decision"]["approver"] == "gippo"

    def test_404_for_unknown_run(self, client):
        resp = client.post("/approvals/nope/decision", json={"approved": False})
        assert resp.status_code == 404


class TestResume:
    def test_resume_calls_orchestrator(self, client, store, monkeypatch):
        import anyio

        anyio.run(lambda: _paused_run(store))

        fake_agent = AsyncMock()
        fake_agent.process = AsyncMock(return_value={"response": "done"})

        class _FakeChatService:
            agent = fake_agent

        import core.chat as chat_module

        monkeypatch.setattr(chat_module, "chat_service", _FakeChatService())
        resp = client.post("/approvals/run-1/resume")
        assert resp.status_code == 200
        assert resp.json()["result"]["response"] == "done"
        fake_agent.process.assert_awaited_once_with(
            "wipe the table", run_id="run-1", resume=True
        )

    def test_resume_404_for_unknown_run(self, client):
        resp = client.post("/approvals/nope/resume")
        assert resp.status_code == 404
