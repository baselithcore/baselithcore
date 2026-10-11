"""The online-evaluation summary route (api_routers plugin)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

import baselith.core.config.evaluation as eval_cfg
from baselith.core.config.evaluation import EvaluationConfig
from baselith.core.evaluation.service import EvaluationService
from baselith.core.events import EventBus


def _client(service: Any) -> TestClient:
    from baselith.plugins.api_routers.admin import verify_credentials
    from baselith.plugins.api_routers.evaluation import router

    app = FastAPI()
    app.include_router(router)
    app.state.evaluation_service = service
    app.dependency_overrides[verify_credentials] = lambda: "admin"
    return TestClient(app)


def test_summary_is_503_when_not_running() -> None:
    assert _client(None).get("/evaluation/online/summary").status_code == 503
    stopped = EvaluationService(event_bus=EventBus(), config=EvaluationConfig())
    assert _client(stopped).get("/evaluation/online/summary").status_code == 503


async def test_summary_reports_intent_windows() -> None:
    from baselith.core.evaluation.online_checks import OnlineSample

    service = EvaluationService(
        event_bus=EventBus(),
        config=EvaluationConfig(enabled=True, online_sample_rate=0.1),
    )
    service.start()
    await service.process_sample(
        OnlineSample(intent="qa", query="q", response="a sufficiently long answer here")
    )
    body = _client(service).get("/evaluation/online/summary").json()
    assert body["running"] is True and body["sample_rate"] == 0.1
    (row,) = body["intents"]
    assert row == {
        "intent": "qa",
        "source": "checks",
        "window_mean": 1.0,
        "window_samples": 1,
        "reference_mean": None,
        "observed": 1,
        "degraded": False,
    }
    await service.shutdown()


def test_summary_requires_admin_credentials() -> None:
    from baselith.plugins.api_routers.evaluation import router

    app = FastAPI()
    app.include_router(router)
    assert TestClient(app).get("/evaluation/online/summary").status_code == 401


def test_router_is_mounted_only_when_online_evaluation_is_on(monkeypatch) -> None:
    from baselith.plugins.api_routers.plugin import ApiRoutersPlugin

    def paths(online: bool) -> set[str]:
        monkeypatch.setattr(
            eval_cfg, "_evaluation_config", EvaluationConfig(online_enabled=online)
        )
        app = FastAPI()
        for router in ApiRoutersPlugin().get_routers():
            app.include_router(router)
        return set(app.openapi()["paths"])

    assert "/v1/evaluation/online/summary" in paths(True)
    assert not any("evaluation" in p for p in paths(False))
