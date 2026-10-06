"""Open (``Any``) response values keep the pre-model ``jsonable_encoder`` wire.

A response model serializes ``Any`` values with pydantic's rules, which differ
from the encoder the routes used before they had models: ``Decimal`` became a
string, a UTC ``datetime`` ended in ``Z`` instead of ``+00:00``, and an
arbitrary object raised ``PydanticSerializationError`` (a 500). Each route
whose model carries such values must render them exactly as
``jsonable_encoder`` does.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.testclient import TestClient

import core.chat as chat_package
import plugins.api_routers.approvals as approvals_module
import plugins.api_routers.privacy as privacy_module
import plugins.api_routers.runs as runs_module
from core.middleware import require_admin_or_job, require_user
from core.orchestration.checkpoint import Checkpoint, InMemoryCheckpointStore
from core.privacy.types import SubjectExport
from plugins.api_routers.admin import verify_credentials

pytestmark = [pytest.mark.unit]


class _Plain:
    """An arbitrary object pydantic cannot serialize but the encoder can."""

    def __init__(self) -> None:
        self.name = "plain"
        self.size = 3


_WHEN = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _exotic() -> dict[str, Any]:
    return {
        "amount": Decimal("1.5"),
        "whole": Decimal("2"),
        "at": _WHEN,
        "obj": _Plain(),
    }


def _client(router: Any) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    for dep in (require_user, require_admin_or_job, verify_credentials):
        app.dependency_overrides[dep] = lambda: "admin"
    return TestClient(app)


def _assert_encoded_as_before(body: Any, raw: Any) -> None:
    assert json.dumps(body) == json.dumps(jsonable_encoder(raw))


def _assert_exotic_spelling(value: dict[str, Any]) -> None:
    assert value["amount"] == 1.5 and isinstance(value["amount"], float)
    assert value["whole"] == 2 and isinstance(value["whole"], int)
    assert value["at"] == "2026-01-02T03:04:05+00:00"
    assert value["obj"] == {"name": "plain", "size": 3}


def test_run_state_encodes_open_values_like_jsonable_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = Checkpoint(run_id="run-1", tenant_id="t", query="q", intent="i").to_dict()
    raw["answer"] = _exotic()
    raw["budget"] = {"cost_usd": Decimal("0.25"), "at": _WHEN}
    raw["plugin_data"] = {"p": _Plain()}
    raw["steps"] = {"s1": {"result": _exotic(), "at": _WHEN}}
    monkeypatch.setattr(
        runs_module, "get_default_checkpoint_store", lambda: InMemoryCheckpointStore()
    )

    async def fake_get_state(store: Any, run_id: str, version: int) -> Any:
        return SimpleNamespace(to_dict=lambda: raw)

    monkeypatch.setattr(runs_module, "get_state", fake_get_state)

    resp = _client(runs_module.router).get("/runs/run-1/history/1")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_encoded_as_before(body, raw)
    _assert_exotic_spelling(body["answer"])
    _assert_exotic_spelling(body["steps"]["s1"]["result"])
    assert body["budget"] == {"cost_usd": 0.25, "at": "2026-01-02T03:04:05+00:00"}
    assert body["plugin_data"] == {"p": {"name": "plain", "size": 3}}


def test_resume_result_encodes_like_jsonable_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryCheckpointStore()
    anyio.run(store.save, Checkpoint(run_id="run-1", tenant_id=None, query="q"))
    monkeypatch.setattr(approvals_module, "get_default_checkpoint_store", lambda: store)
    result = _exotic()
    agent = SimpleNamespace(process=AsyncMock(return_value=result))
    monkeypatch.setattr(
        chat_package, "chat_service", SimpleNamespace(agent=agent), raising=False
    )

    resp = _client(approvals_module.router).post("/approvals/run-1/resume")

    assert resp.status_code == 200, resp.text
    _assert_encoded_as_before(resp.json(), {"run_id": "run-1", "result": result})
    _assert_exotic_spelling(resp.json()["result"])


def test_privacy_export_data_encodes_like_jsonable_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = SubjectExport(
        subject_id="u-1", generated_at=1700000000.5, data={"postgres": _exotic()}
    )
    service = SimpleNamespace(export_subject=AsyncMock(return_value=bundle))
    monkeypatch.setattr(privacy_module, "get_data_subject_service", lambda: service)
    monkeypatch.setattr(privacy_module, "_enforce", lambda request: None)

    resp = _client(privacy_module.router).post(
        "/privacy/export", json={"subject_id": "u-1"}
    )

    assert resp.status_code == 200, resp.text
    _assert_encoded_as_before(resp.json(), bundle.model_dump())
    _assert_exotic_spelling(resp.json()["data"]["postgres"])
