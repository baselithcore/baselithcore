"""Response models on the chat, feedback, async-run, index, prompt and privacy routes.

Each route now declares a ``response_model`` so OpenAPI carries a typed 2xx
schema. The models are descriptive only: every test here drives the route with
a representative payload and asserts the body on the wire is exactly what the
route sent before it had a model (see :mod:`._wire_helpers`).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plugins.api_routers.async_runs as async_runs_module
import plugins.api_routers.chat as chat_module
import plugins.api_routers.feedback as feedback_module
import plugins.api_routers.index as index_module
import plugins.api_routers.privacy as privacy_module
import plugins.api_routers.prompts as prompts_module
from core.middleware import require_admin_or_job, require_user
from core.models.chat import ChatResponse
from core.privacy.types import ErasureReport, RetentionReport, SubjectExport
from core.prompts.registry import PromptRegistry
from core.prompts.sync import PromptSynchronizer
from plugins.api_routers.admin import verify_credentials
from tests.unit.core.prompts.test_prompt_sync import FakeBackend
from tests.unit.plugins_tests._wire_helpers import (
    WireSpy,
    assert_problem_documented,
    assert_typed,
)

pytestmark = [pytest.mark.unit]


def _client(*routers: Any) -> TestClient:
    app = FastAPI()
    for router in routers:
        app.include_router(router)
    for dep in (require_user, require_admin_or_job, verify_credentials):
        app.dependency_overrides[dep] = lambda: "admin"
    return TestClient(app)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> WireSpy:
    return WireSpy(monkeypatch)


class TestChat:
    @pytest.mark.parametrize(
        "result",
        [
            ChatResponse(answer="hi"),
            ChatResponse(
                answer="hi",
                metadata={"latency_ms": 12, "score": 0.5},
                sources=[{"id": "doc-1", "score": 1}],
                conversation_id="c-1",
                cached=True,  # extra key: ChatResponse allows extras
            ),
        ],
    )
    def test_body_unchanged(
        self, spy: WireSpy, monkeypatch: pytest.MonkeyPatch, result: ChatResponse
    ) -> None:
        monkeypatch.setattr(
            chat_module.chat_service,
            "handle_chat_async",
            AsyncMock(return_value=result),
        )
        monkeypatch.setattr(
            chat_module,
            "get_transparency_service",
            lambda: SimpleNamespace(enabled=False),
        )
        resp = _client(chat_module.router).post("/chat", json={"query": "q"})
        assert resp.status_code == 200
        spy.assert_unchanged(resp)
        if result.model_extra:
            assert resp.json()["cached"] is True


class TestFeedback:
    @pytest.mark.parametrize("comment", [None, "great answer"])
    def test_body_unchanged(
        self, spy: WireSpy, monkeypatch: pytest.MonkeyPatch, comment: str | None
    ) -> None:
        service = SimpleNamespace(insert_feedback=AsyncMock())
        monkeypatch.setattr(feedback_module, "get_feedback_service", lambda: service)
        payload: dict[str, Any] = {
            "query": "q",
            "answer": "a",
            "feedback": "positive",
            "conversation_id": "c-1",
            "sources": [{"title": "Doc", "url": None}],
        }
        if comment:
            payload["comment"] = comment
        resp = _client(feedback_module.router).post("/feedback", json=payload)
        assert resp.status_code == 200
        spy.assert_unchanged(resp)
        # An absent comment stays absent; it is not rendered as null.
        assert ("comment" in resp.json()["received"]) is bool(comment)

    def test_legacy_payload_body_unchanged(
        self, spy: WireSpy, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = SimpleNamespace(insert_feedback=AsyncMock())
        monkeypatch.setattr(feedback_module, "get_feedback_service", lambda: service)
        resp = _client(feedback_module.router).post(
            "/feedback", json={"query": "q", "feedback": "negative", "extra": 1}
        )
        assert resp.status_code == 200
        spy.assert_unchanged(resp)


class TestAsyncRuns:
    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        records = {
            "running": {
                "status": "running",
                "progress": 0.0,
                "message": "Task started",
                "updated_at": "2026-10-05T00:00:00+00:00",
                "tenant_id": "default",
            },
            "done": {
                "status": "completed",
                "progress": 100.0,
                "message": "done",
                "updated_at": "2026-10-05T00:00:01+00:00",
                "result": {"answer": "x", "n": 1},
                "tenant_id": "default",
                "custom": "kept",
            },
        }
        tracker = SimpleNamespace(
            get_status_for_tenant=lambda task_id, tenant_id: records.get(task_id)
        )
        monkeypatch.setattr(async_runs_module, "_enqueue", lambda q, c: "job-1")
        monkeypatch.setattr(async_runs_module, "_tracker", lambda: tracker)
        return _client(async_runs_module.router)

    def test_submit_body_unchanged(self, spy: WireSpy, client: TestClient) -> None:
        resp = client.post("/agent/async", json={"query": "q"})
        assert resp.status_code == 202
        spy.assert_unchanged(resp)

    @pytest.mark.parametrize("task_id", ["running", "done"])
    def test_status_body_unchanged(
        self, spy: WireSpy, client: TestClient, task_id: str
    ) -> None:
        resp = client.get(f"/agent/status/{task_id}")
        assert resp.status_code == 200
        spy.assert_unchanged(resp)
        if task_id == "running":
            assert "result" not in resp.json() and "error" not in resp.json()


class TestIndex:
    _STATUS = {
        "bootstrapped": True,
        "running": False,
        "mode": None,
        "error": None,
        "last_completed": "incremental",
        "last_new_documents": 3,
    }

    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        fake = SimpleNamespace(
            status=lambda: dict(self._STATUS, mode="full"),
            schedule=AsyncMock(return_value=True),
            schedule_manual=AsyncMock(return_value=True),
        )
        monkeypatch.setattr(index_module, "bootstrapper", fake)
        monkeypatch.setattr(index_module, "INDEX_BOOTSTRAP_ENABLED", True)
        return _client(index_module.router)

    def test_status_body_unchanged(self, spy: WireSpy, client: TestClient) -> None:
        resp = client.get("/index/status")
        assert resp.status_code == 200
        spy.assert_unchanged(resp)

    def test_reindex_body_unchanged(self, spy: WireSpy, client: TestClient) -> None:
        resp = client.post("/reindex")
        assert resp.status_code == 202
        spy.assert_unchanged(resp)
        assert set(resp.json()) == {"status", "mode", "status_url"}

    def test_bootstrap_body_unchanged(self, spy: WireSpy, client: TestClient) -> None:
        resp = client.post("/index/bootstrap")
        assert resp.status_code == 202
        spy.assert_unchanged(resp)


class TestPrompts:
    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        registry = PromptRegistry()
        syncer = PromptSynchronizer(registry=registry, backend=FakeBackend())
        monkeypatch.setattr(prompts_module, "get_prompt_synchronizer", lambda: syncer)
        monkeypatch.setattr(prompts_module, "get_prompt_registry", lambda: registry)
        return _client(prompts_module.router)

    def test_bodies_unchanged(self, spy: WireSpy, client: TestClient) -> None:
        resp = client.post(
            "/prompts/greet/versions",
            json={"version": "1", "template": "Hi {name}", "labels": ["prod"]},
        )
        assert resp.status_code == 201
        spy.assert_unchanged(resp)
        client.post("/prompts/greet/versions", json={"version": "2", "template": "Yo"})
        resp = client.post("/prompts/greet/labels/prod", json={"version": "2"})
        assert resp.status_code == 200
        spy.assert_unchanged(resp)
        resp = client.get("/prompts?limit=1")
        assert resp.status_code == 200
        spy.assert_unchanged(resp)
        assert resp.json()["prompts"][0]["labels"] == {"prod": "2"}


class TestPrivacy:
    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        service = SimpleNamespace(
            registry=SimpleNamespace(all=lambda: [SimpleNamespace(name="postgres")]),
            export_subject=AsyncMock(
                return_value=SubjectExport(
                    subject_id="u-1",
                    generated_at=1700000000,
                    data={"postgres": [{"id": 1}]},
                )
            ),
            erase_subject=AsyncMock(
                return_value=ErasureReport(
                    subject_id="u-1",
                    completed_at=1700000000.5,
                    erased={"postgres": 2},
                    failed=["vector"],
                )
            ),
            purge_expired=AsyncMock(
                return_value=RetentionReport(
                    older_than_seconds=86400, purged={"postgres": 4}
                )
            ),
        )
        monkeypatch.setattr(privacy_module, "get_data_subject_service", lambda: service)
        monkeypatch.setattr(privacy_module, "_enforce", lambda request: None)
        return _client(privacy_module.router)

    @pytest.mark.parametrize(
        ("method", "path", "body", "status"),
        [
            ("get", "/privacy/providers", None, 200),
            ("post", "/privacy/export", {"subject_id": "u-1"}, 200),
            ("post", "/privacy/erase", {"subject_id": "u-1"}, 200),
            ("post", "/privacy/retention/sweep", {"older_than_days": 1}, 202),
        ],
    )
    def test_body_unchanged(
        self,
        spy: WireSpy,
        client: TestClient,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        status: int,
    ) -> None:
        resp = client.request(method, path, json=body)
        assert resp.status_code == status
        spy.assert_unchanged(resp)


def test_openapi_documents_typed_2xx_and_problem_errors() -> None:
    openapi = _client(
        chat_module.router,
        feedback_module.router,
        async_runs_module.router,
        index_module.router,
        prompts_module.router,
        privacy_module.router,
    ).app.openapi()
    for path, method in [
        ("/chat", "post"),
        ("/feedback", "post"),
        ("/agent/async", "post"),
        ("/agent/status/{task_id}", "get"),
        ("/index/status", "get"),
        ("/index/bootstrap", "post"),
        ("/reindex", "post"),
        ("/prompts", "get"),
        ("/prompts/{name}/versions", "post"),
        ("/prompts/{name}/labels/{label}", "post"),
        ("/privacy/providers", "get"),
        ("/privacy/export", "post"),
        ("/privacy/erase", "post"),
        ("/privacy/retention/sweep", "post"),
    ]:
        assert_typed(openapi, path, method)
        assert_problem_documented(openapi, path, method, "401")
    assert_problem_documented(openapi, "/chat", "post", "422")
    assert_problem_documented(openapi, "/agent/status/{task_id}", "get", "404")
    problem = openapi["components"]["schemas"]["ProblemDetails"]
    assert {"type", "title", "status", "code"} <= set(problem["required"])
