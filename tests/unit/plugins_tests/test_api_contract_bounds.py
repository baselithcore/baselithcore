"""List endpoints are cursor-paginated and request payloads are bounded.

Several list endpoints returned the whole collection in one body, and several
payload fields (ids, URLs, lists, header maps, a free-form ``context`` dict)
had no size bound at all. Lists now share one contract — ``limit`` (1..200,
visible in OpenAPI) + opaque ``cursor``, answered with ``next_cursor`` and
``has_more`` beside the historical list key and ``count`` — and payloads carry
explicit limits that FastAPI turns into 422s.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from core.api.errors import install_error_handlers
from core.api.pagination import MAX_LIMIT, PageParams, page_params, paginated
from core.models.chat import MAX_ID_LENGTH, ChatRequest

# --- the shared helper ----------------------------------------------------------


def _list_app() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/things")
    async def things(page: PageParams = Depends(page_params)) -> dict[str, Any]:
        return paginated(list(range(7)), page, key="things", serialize=str)

    return TestClient(app)


class TestPaginatedHelper:
    def test_walks_every_item_exactly_once(self) -> None:
        client = _list_app()
        seen: list[str] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 3}
            if cursor:
                params["cursor"] = cursor
            body = client.get("/things", params=params).json()
            assert body["count"] == len(body["things"]) <= 3
            seen += body["things"]
            cursor = body["next_cursor"]
            if not body["has_more"]:
                assert cursor is None
                break
        assert seen == [str(i) for i in range(7)]

    def test_default_page_and_shape(self) -> None:
        body = _list_app().get("/things").json()
        assert body == {
            "things": [str(i) for i in range(7)],
            "count": 7,
            "next_cursor": None,
            "has_more": False,
        }

    def test_limit_cap_is_enforced_and_published(self) -> None:
        client = _list_app()
        assert client.get("/things", params={"limit": MAX_LIMIT + 1}).status_code == 422
        assert client.get("/things", params={"limit": 0}).status_code == 422
        params = client.get("/openapi.json").json()["paths"]["/things"]["get"][
            "parameters"
        ]
        limit = next(p for p in params if p["name"] == "limit")
        schema = limit["schema"]
        bounds = schema.get("anyOf", [schema])[0]
        assert bounds["maximum"] == MAX_LIMIT
        assert bounds["minimum"] == 1

    def test_bad_cursor_is_a_400_problem(self) -> None:
        resp = _list_app().get("/things", params={"cursor": "not-a-cursor"})
        assert resp.status_code == 400
        assert resp.headers["content-type"].startswith("application/problem+json")


# --- webhooks --------------------------------------------------------------------


class TestWebhookBounds:
    def test_payload_limits(self) -> None:
        from plugins.api_routers.webhooks import CreateWebhookRequest

        ok = {"url": "https://h.test/x"}
        CreateWebhookRequest(**ok)
        bad: list[dict[str, Any]] = [
            {"url": "https://h.test/" + "a" * 2100},
            {**ok, "description": "d" * 2001},
            {**ok, "event_types": [f"e{i}" for i in range(101)]},
            {**ok, "event_types": ["x" * 129]},
            {**ok, "headers": {f"X-H{i}": "v" for i in range(21)}},
            {**ok, "headers": {"X-H": "v" * 4097}},
            {**ok, "headers": {"X" * 257: "v"}},
        ]
        for payload in bad:
            with pytest.raises(ValidationError):
                CreateWebhookRequest(**payload)

    def test_list_endpoints_paginate(self) -> None:
        from plugins.api_routers import webhooks

        app = FastAPI()
        app.include_router(webhooks.router)
        paths = app.openapi()["paths"]
        for path in ("/webhooks", "/webhooks/deliveries"):
            names = {p["name"] for p in paths[path]["get"]["parameters"]}
            assert {"limit", "cursor"} <= names, path


# --- compliance ------------------------------------------------------------------


class TestComplianceBounds:
    def test_register_payload_limits(self) -> None:
        from plugins.api_routers.compliance import RegisterSystemRequest

        RegisterSystemRequest(name="s")
        for payload in (
            {"name": "n" * 201},
            {"name": "s", "description": "d" * 10_001},
            {"name": "s", "models": [f"m{i}" for i in range(101)]},
            {"name": "s", "deployers": ["x" * 201]},
        ):
            with pytest.raises(ValidationError):
                RegisterSystemRequest(**payload)

    def test_observation_context_is_size_bounded(self) -> None:
        from plugins.api_routers.compliance import ObservationRequest

        ObservationRequest(metric="m", value=1.0, context={"k": "v"})
        with pytest.raises(ValidationError):
            ObservationRequest(metric="m", value=1.0, context={"k": "v" * 20_000})
        with pytest.raises(ValidationError):
            ObservationRequest(
                metric="m", value=1.0, context={f"k{i}": i for i in range(51)}
            )

    def test_every_list_endpoint_paginates(self) -> None:
        from plugins.api_routers import compliance, compliance_artefacts

        app = FastAPI()
        app.include_router(compliance.router)
        app.include_router(compliance_artefacts.router)
        paths = app.openapi()["paths"]
        listing = [
            "/compliance/systems",
            "/compliance/pending-registration",
            "/compliance/documentation",
            "/compliance/fria",
            "/compliance/ropa",
            "/compliance/post-market",
            "/compliance/risk-management",
            "/compliance/instructions",
            "/compliance/dpia",
            "/compliance/automated-decisions",
        ]
        for path in listing:
            names = {p["name"] for p in paths[path]["get"]["parameters"]}
            assert {"limit", "cursor"} <= names, path


# --- prompts ---------------------------------------------------------------------


class TestPrompts:
    def test_list_bounds(self) -> None:
        from plugins.api_routers.prompts import PromptVersionIn

        PromptVersionIn(version="1", template="t", labels=["prod"])
        for payload in (
            {"labels": [f"l{i}" for i in range(51)]},
            {"labels": ["x" * 101]},
            {"variables": [f"v{i}" for i in range(201)]},
        ):
            with pytest.raises(ValidationError):
                PromptVersionIn(version="1", template="t", **payload)

    def test_listing_is_paginated_with_total(self, monkeypatch: Any) -> None:
        import plugins.api_routers.prompts as prompts_module
        from core.prompts.registry import PromptRegistry
        from core.prompts.types import PromptVersion
        from plugins.api_routers.admin import verify_credentials

        registry = PromptRegistry()
        for name in ("c", "a", "b"):
            registry.store.put(PromptVersion(name=name, version="1", template="t"))
        monkeypatch.setattr(prompts_module, "get_prompt_registry", lambda: registry)
        app = FastAPI()
        app.include_router(prompts_module.router)
        app.dependency_overrides[verify_credentials] = lambda: "admin"
        client = TestClient(app)

        first = client.get("/prompts", params={"limit": 2}).json()
        assert [p["name"] for p in first["prompts"]] == ["a", "b"]
        assert first["total"] == 3 and first["count"] == 2 and first["has_more"]
        rest = client.get(
            "/prompts", params={"limit": 2, "cursor": first["next_cursor"]}
        ).json()
        assert [p["name"] for p in rest["prompts"]] == ["c"]
        assert rest["has_more"] is False


# --- ids ---------------------------------------------------------------------------


class TestIdBounds:
    @pytest.mark.parametrize("field", ["conversation_id", "kb_label", "tenant_id"])
    def test_chat_request_ids_are_bounded(self, field: str) -> None:
        ChatRequest(query="q", **{field: "x" * MAX_ID_LENGTH})
        with pytest.raises(ValidationError):
            ChatRequest(query="q", **{field: "x" * (MAX_ID_LENGTH + 1)})

    def test_tenant_create_payload_is_bounded(self) -> None:
        from plugins.api_routers.tenant import CreateTenantRequest

        CreateTenantRequest(id="acme", name="Acme")
        for payload in (
            {"id": "", "name": "n"},
            {"id": "x" * (MAX_ID_LENGTH + 1), "name": "n"},
            {"id": "acme", "name": "n" * 257},
        ):
            with pytest.raises(ValidationError):
                CreateTenantRequest(**payload)
