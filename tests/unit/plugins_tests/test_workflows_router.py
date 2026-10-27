"""Tests for the /workflows API and the signed inbound trigger."""

from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from baselith.core.config.workflows import WorkflowsConfig
from baselith.core.webhooks.signing import build_signature_header
from baselith.core.workflows.builder import WorkflowBuilder, WorkflowDefinition
from baselith.core.workflows.service import WorkflowService, set_workflow_service
from baselith.core.workflows.triggers import derive_workflow_secret
from baselith.plugins.api_routers.routes.admin import verify_credentials
from baselith.plugins.api_routers.routes.workflows import router, trigger_router

MASTER = "trigger-master"


def _wf(workflow_id: str = "greet", **fields) -> WorkflowDefinition:
    wf = WorkflowBuilder("g").start().template("t", "hi {{ input }}").end().build()
    wf.id = workflow_id
    for key, value in fields.items():
        setattr(wf, key, value)
    return wf


@pytest.fixture
def service(tmp_path):
    config = WorkflowsConfig(
        WORKFLOWS_ENABLED="true",
        WORKFLOWS_DIR=str(tmp_path),
        WORKFLOWS_TRIGGER_SECRET=MASTER,
    )
    svc = WorkflowService(config)
    set_workflow_service(svc)
    yield svc
    set_workflow_service(None)


@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(router)
    app.include_router(trigger_router)
    app.dependency_overrides[verify_credentials] = lambda: "admin"
    return TestClient(app)


def _sign(workflow_id: str, body: bytes, *, ts: int | None = None) -> dict[str, str]:
    header = build_signature_header(
        derive_workflow_secret(MASTER, workflow_id), body, timestamp=ts
    )
    return {"X-Baselith-Signature": header, "Content-Type": "application/json"}


class TestCrud:
    def test_save_get_list_delete_round_trip(self, client, service, tmp_path):
        wf = _wf("greet", description="says hi")
        resp = client.put("/workflows/greet", content=wf.to_json())
        assert resp.status_code == 200
        assert resp.json() == {"id": "greet", "persisted": True}
        assert (tmp_path / "greet.yaml").is_file()

        got = client.get("/workflows/greet").json()
        assert got["id"] == "greet" and got["description"] == "says hi"
        assert len(got["nodes"]) == 3

        listing = client.get("/workflows").json()
        assert listing["count"] == 1
        summary = listing["workflows"][0]
        assert summary["id"] == "greet" and summary["nodes"] == 3
        assert summary["webhook_trigger"] is False and summary["mcp_exposed"] is False

        assert client.delete("/workflows/greet").json() == {
            "id": "greet",
            "removed": True,
        }
        assert not (tmp_path / "greet.yaml").exists()
        assert client.get("/workflows/greet").status_code == 404
        assert client.delete("/workflows/greet").status_code == 404

    def test_yaml_body_is_accepted_and_id_comes_from_the_path(self, client):
        yaml_body = _wf("whatever").to_yaml().replace("id: whatever\n", "", 1)
        resp = client.put("/workflows/from-yaml", content=yaml_body)
        assert resp.status_code == 200, resp.text
        assert client.get("/workflows/from-yaml").json()["id"] == "from-yaml"

    def test_body_id_must_match_the_path(self, client):
        resp = client.put("/workflows/one", content=_wf("two").to_json())
        assert resp.status_code == 422 and "does not match" in resp.json()["detail"]

    @pytest.mark.parametrize("bad", ["a b", "-x", "x" * 101])
    def test_invalid_path_id(self, client, bad):
        resp = client.put(f"/workflows/{bad}", content=_wf("ok").to_json())
        assert resp.status_code == 422

    def test_invalid_definition_is_422_with_the_reason(self, client):
        broken = WorkflowDefinition(id="broken", name="b").to_json()
        resp = client.put("/workflows/broken", content=broken)
        assert resp.status_code == 422 and "START" in resp.json()["detail"]
        assert client.get("/workflows/broken").status_code == 404

    def test_unparseable_and_hostile_bodies(self, client):
        assert client.put("/workflows/x", content="- a\n- b\n").status_code == 422
        assert (
            client.put("/workflows/x", content="a: &a [1]\nb: *a\n").status_code == 422
        )
        assert client.put("/workflows/x", content=b"\xff\xfe").status_code == 422

    def test_oversized_body_is_413(self, client):
        resp = client.put("/workflows/x", content="a" * (2 * 1024 * 1024 + 10))
        assert resp.status_code == 413

    def test_validate_does_not_save(self, client):
        ok = client.post("/workflows/validate", content=_wf("v").to_json()).json()
        assert ok == {"valid": True, "errors": []}
        bad = client.post(
            "/workflows/validate", content=WorkflowDefinition(name="b").to_json()
        ).json()
        assert bad["valid"] is False and bad["errors"]
        assert client.get("/workflows/v").status_code == 404

    def test_pagination(self, client, service):
        for i in range(5):
            service.catalog.register(_wf(f"w{i}"))
        page = client.get("/workflows?limit=2").json()
        assert page["count"] == 2 and page["has_more"] is True
        rest = client.get(f"/workflows?limit=10&cursor={page['next_cursor']}").json()
        assert [w["id"] for w in rest["workflows"]] == ["w2", "w3", "w4"]


class TestRun:
    def test_run_waits_and_returns_the_result(self, client, service):
        service.catalog.register(_wf())
        resp = client.post("/workflows/greet/run", json={"input": "Ada"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed" and body["output"] == "hi Ada"
        assert body["source"] == "api"
        assert (
            client.get(f"/workflows/runs/{body['run_id']}").json()["output"] == "hi Ada"
        )
        assert client.get("/workflows/runs").json()["count"] == 1
        assert client.get("/workflows/runs?workflow_id=zzz").json()["count"] == 0

    def test_run_without_a_body_uses_null_input(self, client, service):
        wf = (
            WorkflowBuilder("n")
            .start()
            .template("t", "x{{ input | default('-') }}")
            .end()
            .build()
        )
        wf.id = "n"
        service.catalog.register(wf)
        assert client.post("/workflows/n/run").json()["output"] == "x-"

    def test_wait_false_answers_202(self, client, service):
        service.catalog.register(_wf())
        resp = client.post("/workflows/greet/run", json={"input": "x", "wait": False})
        assert resp.status_code == 202 and resp.json()["status"] == "running"

    def test_failed_run_is_a_200_with_status_failed(self, client, service):
        wf = (
            WorkflowBuilder("f")
            .start()
            .template("t", "{{ nodes.ghost }}")
            .end()
            .build()
        )
        wf.id = "bad"
        service.catalog.register(wf)
        resp = client.post("/workflows/bad/run", json={})
        assert resp.status_code == 200 and resp.json()["status"] == "failed"

    def test_unknown_workflow_and_run(self, client):
        assert client.post("/workflows/nope/run", json={}).status_code == 404
        assert client.get("/workflows/runs/nope").status_code == 404
        assert client.post("/workflows/runs/nope/resume").status_code in (404, 503)

    def test_busy_runtime_is_429_with_retry_after(self, client, service):
        service.catalog.register(_wf())
        service.runtime._inflight = service.config.max_concurrent_runs
        resp = client.post("/workflows/greet/run", json={})
        assert resp.status_code == 429 and resp.headers["retry-after"] == "5"

    def test_static_routes_are_not_shadowed_by_the_id_route(self, client):
        assert client.get("/workflows/runs").status_code == 200
        assert (
            client.post("/workflows/validate", content=_wf("x").to_json()).status_code
            == 200
        )

    def test_disabled_service_is_503(self, client):
        set_workflow_service(None)
        assert client.get("/workflows").status_code == 503


class TestTrigger:
    def _enable(self, service, workflow_id="hook", **fields):
        service.catalog.register(_wf(workflow_id, webhook_trigger=True, **fields))

    def test_signed_request_starts_a_background_run(self, client, service):
        self._enable(service)
        body = b'{"ticket": 7}'
        resp = client.post(
            "/workflows/hook/trigger", content=body, headers=_sign("hook", body)
        )
        assert resp.status_code == 202
        assert resp.json()["source"] == "trigger" and resp.json()["status"] == "running"

    def test_wait_returns_the_finished_run_with_the_parsed_payload(
        self, client, service
    ):
        wf = (
            WorkflowBuilder("t")
            .start()
            .template("t", "ticket {{ input.ticket }}")
            .end()
            .build()
        )
        wf.id, wf.webhook_trigger = "hook", True
        service.catalog.register(wf)
        body = b'{"ticket": 7}'
        resp = client.post(
            "/workflows/hook/trigger?wait=true",
            content=body,
            headers=_sign("hook", body),
        )
        assert resp.status_code == 200 and resp.json()["output"] == "ticket 7"

    def test_bad_signature_is_401_and_hides_whether_the_workflow_exists(
        self, client, service
    ):
        self._enable(service)
        body = b"{}"
        for target in ("hook", "ghost"):
            resp = client.post(
                f"/workflows/{target}/trigger",
                content=body,
                headers={
                    "X-Baselith-Signature": "t=1,v1=00",
                    "Content-Type": "application/json",
                },
            )
            assert resp.status_code == 401

    def test_missing_signature_is_401(self, client, service):
        self._enable(service)
        assert client.post("/workflows/hook/trigger", content=b"{}").status_code == 401

    def test_replay_is_409(self, client, service):
        self._enable(service)
        body = b"{}"
        headers = _sign("hook", body)
        assert (
            client.post(
                "/workflows/hook/trigger", content=body, headers=headers
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/workflows/hook/trigger", content=body, headers=headers
            ).status_code
            == 409
        )

    def test_signature_of_another_workflow_is_refused(self, client, service):
        self._enable(service, "hook")
        self._enable(service, "other")
        body = b"{}"
        resp = client.post(
            "/workflows/other/trigger", content=body, headers=_sign("hook", body)
        )
        assert resp.status_code == 401

    def test_workflow_without_opt_in_is_404_after_authentication(self, client, service):
        service.catalog.register(_wf("closed"))
        body = b"{}"
        resp = client.post(
            "/workflows/closed/trigger", content=body, headers=_sign("closed", body)
        )
        assert resp.status_code == 404

    def test_stale_signature_is_401(self, client, service):
        self._enable(service)
        body = b"{}"
        stale = _sign("hook", body, ts=int(time.time()) - 3600)
        assert (
            client.post(
                "/workflows/hook/trigger", content=body, headers=stale
            ).status_code
            == 401
        )

    def test_unconfigured_secret_is_503(self, client, service):
        service.verifier._secret = None
        self._enable(service)
        body = b"{}"
        resp = client.post(
            "/workflows/hook/trigger", content=body, headers=_sign("hook", body)
        )
        assert resp.status_code == 503

    def test_oversized_body_is_413_before_any_hashing(self, client, service):
        self._enable(service)
        assert (
            client.post(
                "/workflows/hook/trigger", content=b"a" * (1024 * 1024 + 1)
            ).status_code
            == 413
        )

    def test_trigger_needs_no_admin_credentials(self, service):
        app = FastAPI()
        app.include_router(router)
        app.include_router(trigger_router)  # no dependency override at all
        client = TestClient(app)
        self._enable(service)
        body = b"{}"
        assert (
            client.post(
                "/workflows/hook/trigger", content=body, headers=_sign("hook", body)
            ).status_code
            == 202
        )
        # ...whereas the admin surface still demands credentials.
        assert client.get("/workflows").status_code in (401, 403, 503)

    def test_non_json_body_becomes_text_input(self, client, service):
        wf = WorkflowBuilder("t").start().template("t", "got {{ input }}").end().build()
        wf.id, wf.webhook_trigger = "hook", True
        service.catalog.register(wf)
        body = b"plain text"
        headers = {**_sign("hook", body), "Content-Type": "text/plain"}
        resp = client.post(
            "/workflows/hook/trigger?wait=true", content=body, headers=headers
        )
        assert resp.json()["output"] == "got plain text"

    def test_declared_json_that_is_not_json_is_422(self, client, service):
        self._enable(service)
        body = b"{oops"
        assert (
            client.post(
                "/workflows/hook/trigger", content=body, headers=_sign("hook", body)
            ).status_code
            == 422
        )

    def test_secret_value_never_appears_in_responses(self, client, service):
        self._enable(service)
        body = b"{}"
        resp = client.post(
            "/workflows/hook/trigger", content=body, headers=_sign("hook", body)
        )
        assert MASTER not in resp.text
        assert isinstance(service.config.trigger_secret, SecretStr)


def test_routers_are_mounted_only_when_workflows_are_enabled(monkeypatch) -> None:
    import baselith.core.config.workflows as workflows_cfg
    from baselith.plugins.api_routers.plugin import ApiRoutersPlugin

    def paths(enabled: bool) -> set[str]:
        monkeypatch.setattr(
            workflows_cfg,
            "_workflows_config",
            WorkflowsConfig(WORKFLOWS_ENABLED="true" if enabled else "false"),
        )
        app = FastAPI()
        for mounted in ApiRoutersPlugin().get_routers():
            app.include_router(mounted)
        return set(app.openapi()["paths"])

    on = paths(True)
    assert "/v1/workflows" in on
    assert "/v1/workflows/{workflow_id}/trigger" in on
    assert "/v1/workflows/runs/{run_id}/resume" in on
    assert not any("workflows" in p for p in paths(False))


class TestGenerate:
    def test_returns_a_validated_draft_and_saves_nothing(
        self, client, service, monkeypatch
    ):
        from baselith.core.workflows.generator import GenerationResult

        draft = _wf("drafted")
        seen = {}

        async def fake(description, *, existing, max_attempts):
            seen.update(
                description=description, existing=existing, attempts=max_attempts
            )
            return GenerationResult(draft, draft.to_yaml(), 2, ["note"])

        monkeypatch.setattr(
            "baselith.plugins.api_routers.routes.workflows.generate_workflow", fake
        )
        resp = client.post(
            "/workflows/generate", json={"description": "say hi", "max_attempts": 4}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["attempts"] == 2 and body["warnings"] == ["note"]
        assert body["workflow"]["id"] == "drafted" and "id: drafted" in body["yaml"]
        assert seen == {"description": "say hi", "existing": None, "attempts": 4}
        assert service.catalog.get("drafted") is None

    def test_revising_a_catalog_workflow(self, client, service, monkeypatch):
        from baselith.core.workflows.generator import GenerationResult

        service.catalog.register(_wf("current"))
        seen = {}

        async def fake(description, *, existing, max_attempts):
            seen["existing"] = existing
            return GenerationResult(existing, existing.to_yaml(), 1, [])

        monkeypatch.setattr(
            "baselith.plugins.api_routers.routes.workflows.generate_workflow", fake
        )
        resp = client.post(
            "/workflows/generate", json={"description": "tweak", "revise_id": "current"}
        )
        assert resp.status_code == 200 and seen["existing"].id == "current"
        assert (
            client.post(
                "/workflows/generate", json={"description": "x", "revise_id": "nope"}
            ).status_code
            == 404
        )

    def test_an_invalid_draft_is_422_with_the_errors(self, client, monkeypatch):
        from baselith.core.workflows.generator import GenerationError

        async def fake(description, *, existing, max_attempts):
            raise GenerationError(["Workflow must have at least one END node"])

        monkeypatch.setattr(
            "baselith.plugins.api_routers.routes.workflows.generate_workflow", fake
        )
        resp = client.post("/workflows/generate", json={"description": "x"})
        assert resp.status_code == 422
        assert resp.json()["detail"]["errors"] == [
            "Workflow must have at least one END node"
        ]

    def test_a_model_failure_is_502_without_its_text(self, client, monkeypatch):
        async def fake(description, *, existing, max_attempts):
            raise RuntimeError("provider said: key sk-secret rejected")

        monkeypatch.setattr(
            "baselith.plugins.api_routers.routes.workflows.generate_workflow", fake
        )
        resp = client.post("/workflows/generate", json={"description": "x"})
        assert resp.status_code == 502 and "sk-secret" not in resp.text

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"description": ""},
            {"description": "x" * 4001},
            {"description": "x", "max_attempts": 0},
            {"description": "x", "max_attempts": 6},
        ],
    )
    def test_bounds(self, client, payload):
        assert client.post("/workflows/generate", json=payload).status_code == 422

    def test_static_path_is_not_shadowed_by_the_id_routes(self, client):
        assert (
            client.post("/workflows/generate", json={"description": ""}).status_code
            == 422
        )
