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
        assert resp.json() == {
            "pending": [],
            "count": 0,
            "next_cursor": None,
            "has_more": False,
        }

    def test_pages_with_a_cursor(self, client, store):
        import anyio

        async def seed():
            for i in range(5):
                await _paused_run(store, run_id=f"run-{i}")
            running = Checkpoint(run_id="crashed", query="q")  # status running
            await store.save(running)

        anyio.run(seed)
        seen: list[str] = []
        cursor = None
        for _ in range(10):
            params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            body = client.get("/approvals", params=params).json()
            assert body["count"] <= 2
            seen += [e["run_id"] for e in body["pending"]]
            cursor = body["next_cursor"]
            if not body["has_more"]:
                break
        # Every paused run exactly once; the running checkpoint never listed.
        assert sorted(seen) == [f"run-{i}" for i in range(5)]

    def test_limit_is_capped_and_cursor_validated(self, client):
        assert client.get("/approvals", params={"limit": 201}).status_code == 422
        assert client.get("/approvals", params={"limit": 0}).status_code == 422
        assert client.get("/approvals", params={"cursor": "!!"}).status_code == 400

    def test_running_rows_cost_no_loads(self, client, store, monkeypatch):
        """Only pending runs are listed, and only the page is loaded."""
        import anyio

        async def seed():
            for i in range(50):
                await store.save(Checkpoint(run_id=f"busy-{i}", query="q"))
            for i in range(3):
                await _paused_run(store, run_id=f"run-{i}")

        anyio.run(seed)
        loads = 0
        real_load = store.load

        async def counting_load(run_id):
            nonlocal loads
            loads += 1
            return await real_load(run_id)

        monkeypatch.setattr(store, "load", counting_load)
        body = client.get("/approvals", params={"limit": 2}).json()
        assert len(body["pending"]) == 2
        assert loads == 2
        assert body["has_more"] is True and body["next_cursor"]

    def test_a_decision_between_pages_skips_nothing(self, client, store):
        """Keyset cursor: a run leaving the listing never shifts the next page."""
        import anyio

        async def seed():
            for i in range(4):
                checkpoint = await _paused_run(store, run_id=f"run-{i}")
                checkpoint.updated_at = 1000.0 + i
                await store.save(checkpoint)

        anyio.run(seed)
        first = client.get("/approvals", params={"limit": 2}).json()
        first_ids = [e["run_id"] for e in first["pending"]]

        async def decide_first():
            checkpoint = await store.load(first_ids[0])
            checkpoint.status = "running"  # decided and resumed
            await store.save(checkpoint)

        anyio.run(decide_first)
        second = client.get(
            "/approvals", params={"limit": 2, "cursor": first["next_cursor"]}
        ).json()
        seen = first_ids + [e["run_id"] for e in second["pending"]]
        assert sorted(seen) == [f"run-{i}" for i in range(4)]
        assert second["has_more"] is False

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
    """Who approved is decided by the authenticated identity, never by the
    request body: an approval attributed to whatever string the client typed
    answers none of the questions an auditor asks afterwards."""

    def test_records_decision_against_the_authenticated_identity(self, client, store):
        import anyio

        anyio.run(lambda: _paused_run(store))
        resp = client.post(
            "/approvals/run-1/decision",
            json={"approved": True, "reason": "ok"},
        )
        assert resp.status_code == 200
        assert resp.json()["recorded"] is True

        loaded = anyio.run(store.load, "run-1")
        decision = loaded.pending_approval["decision"]
        assert decision["approved"] is True
        assert decision["approver"] == "admin"  # the authenticated user
        assert decision["approver_auth_method"] == "http_basic_admin"

    def test_client_supplied_approver_cannot_forge_the_identity(self, client, store):
        import anyio

        anyio.run(lambda: _paused_run(store))
        resp = client.post(
            "/approvals/run-1/decision",
            json={"approved": True, "approver": "someone-else"},
        )
        assert resp.status_code == 200

        loaded = anyio.run(store.load, "run-1")
        decision = loaded.pending_approval["decision"]
        assert decision["approver"] == "admin"
        assert decision["approver_auth_method"] == "http_basic_admin"
        # Kept only as a display label, clearly separated from the identity.
        assert decision["approver_label"] == "someone-else"

    def test_no_label_when_the_body_omits_one(self, client, store):
        import anyio

        anyio.run(lambda: _paused_run(store))
        client.post("/approvals/run-1/decision", json={"approved": False})
        loaded = anyio.run(store.load, "run-1")
        assert loaded.pending_approval["decision"]["approver_label"] is None

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
            "wipe the table", context={}, run_id="run-1", resume=True
        )

    def test_resume_runs_under_the_checkpoint_owner_tenant(
        self, client, store, monkeypatch
    ):
        """The admin's ambient tenant must not leak into another tenant's run."""
        import anyio

        from core.context import get_current_tenant_id

        anyio.run(lambda: _paused_run(store, tenant="tenant-a"))
        seen: dict[str, object] = {}

        async def _process(query, *, context, run_id, resume):
            seen["ambient"] = get_current_tenant_id()
            seen["context"] = dict(context)
            return {"response": "done"}

        class _FakeChatService:
            agent = AsyncMock()

        _FakeChatService.agent.process = _process
        import core.chat as chat_module

        monkeypatch.setattr(chat_module, "chat_service", _FakeChatService())
        resp = client.post("/approvals/run-1/resume")
        assert resp.status_code == 200
        assert seen["ambient"] == "tenant-a"
        assert seen["context"] == {"tenant_id": "tenant-a"}

    def test_resume_failure_does_not_echo_internals(self, client, store, monkeypatch):
        import anyio

        anyio.run(lambda: _paused_run(store))
        fake_agent = AsyncMock()
        fake_agent.process = AsyncMock(side_effect=RuntimeError("dsn=postgres://x"))

        class _FakeChatService:
            agent = fake_agent

        import core.chat as chat_module

        monkeypatch.setattr(chat_module, "chat_service", _FakeChatService())
        resp = client.post("/approvals/run-1/resume")
        assert resp.status_code == 500
        assert "postgres" not in resp.text

    def test_resume_404_for_unknown_run(self, client):
        resp = client.post("/approvals/nope/resume")
        assert resp.status_code == 404
