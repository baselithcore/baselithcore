"""Tests for the /runs state-history / time-travel API."""

from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plugins.api_routers.runs as runs_module
from core.orchestration.checkpoint import Checkpoint, InMemoryCheckpointStore
from plugins.api_routers.admin import verify_credentials
from plugins.api_routers.runs import router


@pytest.fixture
def store():
    return InMemoryCheckpointStore(history_enabled=True)


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setattr(runs_module, "get_default_checkpoint_store", lambda: store)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_credentials] = lambda: "admin"
    return TestClient(app)


async def _seed_run(store, run_id="run-1"):
    checkpoint = Checkpoint(run_id=run_id, query="hello")
    await store.save(checkpoint)  # v1
    checkpoint.step = 1
    await store.save(checkpoint)  # v2
    return checkpoint


class TestHistory:
    def test_lists_versions(self, client, store):
        anyio.run(lambda: _seed_run(store))
        resp = client.get("/runs/run-1/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == "run-1"
        assert [s["version"] for s in body["history"]] == [1, 2]

    def test_404_for_unknown_run(self, client):
        resp = client.get("/runs/nope/history")
        assert resp.status_code == 404

    def test_503_when_checkpointing_disabled(self, monkeypatch):
        monkeypatch.setattr(runs_module, "get_default_checkpoint_store", lambda: None)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[verify_credentials] = lambda: "admin"
        resp = TestClient(app).get("/runs/run-1/history")
        assert resp.status_code == 503


class TestStateAtVersion:
    def test_returns_full_state(self, client, store):
        anyio.run(lambda: _seed_run(store))
        resp = client.get("/runs/run-1/history/1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == "run-1"
        assert body["query"] == "hello"
        assert body["version"] == 1

    def test_404_for_unknown_version(self, client, store):
        anyio.run(lambda: _seed_run(store))
        resp = client.get("/runs/run-1/history/99")
        assert resp.status_code == 404


class TestEventStream:
    @pytest.mark.asyncio
    async def test_sse_stream_until_terminal(self, monkeypatch):
        import asyncio

        from httpx import ASGITransport, AsyncClient

        from core.api.events import AgentEvent, EventType
        from core.orchestration.run_events import RunEventStream

        stream = RunEventStream()
        monkeypatch.setattr(runs_module, "get_run_event_stream", lambda: stream)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[verify_credentials] = lambda: "admin"

        async def publisher():
            # Let the endpoint subscribe first.
            for _ in range(50):
                await asyncio.sleep(0.01)
                if stream.publish(
                    "r1",
                    AgentEvent(
                        type=EventType.TOOL_CALL, data={"tool_name": "toolA"}
                    ),
                ):
                    break
            stream.publish(
                "r1",
                AgentEvent(type=EventType.RESPONSE_FINAL, data={"response": "done"}),
            )

        task = asyncio.create_task(publisher())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream("GET", "/runs/r1/events") as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                body = ""
                async for chunk in resp.aiter_text():
                    body += chunk
        await task
        assert "event: tool_call" in body
        assert "event: final" in body
        assert '"response":"done"' in body.replace(" ", "")


class TestFork:
    def test_forks_into_new_run(self, client, store):
        anyio.run(lambda: _seed_run(store))
        resp = client.post(
            "/runs/run-1/fork", json={"version": 2, "new_run_id": "fork-1"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_run_id"] == "run-1"
        assert body["run_id"] == "fork-1"

        forked = anyio.run(store.load, "fork-1")
        assert forked is not None and forked.query == "hello"

    def test_fork_404_for_unknown_version(self, client, store):
        anyio.run(lambda: _seed_run(store))
        resp = client.post("/runs/run-1/fork", json={"version": 99})
        assert resp.status_code == 404

    def test_fork_generates_run_id_when_omitted(self, client, store):
        anyio.run(lambda: _seed_run(store))
        resp = client.post("/runs/run-1/fork", json={"version": 1})
        assert resp.status_code == 200
        assert resp.json()["run_id"]
