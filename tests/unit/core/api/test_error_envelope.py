"""Tests for the RFC 9457 problem+json error handlers."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.api.errors import install_error_handlers
from core.auth.types import (
    InsufficientPermissionsError,
    InsufficientScopeError,
)
from core.exceptions import (
    BaselithError,
    ItemNotFoundError,
    PluginConfigError,
    PluginIntegrityError,
)
from core.quotas.manager import QuotaExceededError, QuotaWindow

PROBLEM_JSON = "application/problem+json"


@pytest.fixture
def client():
    app = FastAPI()

    @app.get("/notfound")
    def _nf():
        raise ItemNotFoundError("no such item")

    @app.get("/badconfig")
    def _bc():
        raise PluginConfigError("bad schema")

    @app.get("/forbidden")
    def _fb():
        raise PluginIntegrityError("unsigned")

    @app.get("/no-role")
    def _nr():
        raise InsufficientPermissionsError("needs admin role")

    @app.get("/no-scope")
    def _ns():
        raise InsufficientScopeError(
            "needs webhooks:write", required={"webhooks:write"}
        )

    @app.get("/over-quota")
    def _oq():
        raise QuotaExceededError("k", QuotaWindow.DAILY, 100, 100)

    @app.get("/generic-baselith")
    def _gb():
        raise BaselithError("root failure")

    @app.get("/boom")
    def _boom():
        raise RuntimeError("unexpected")

    @app.get("/http")
    def _http():
        raise HTTPException(status_code=418, detail="teapot")

    @app.get("/unauthorized")
    def _unauth():
        raise HTTPException(
            status_code=401,
            detail="nope",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.get("/stepup")
    def _stepup():
        # Structured detail: machine-readable gates (e.g. step-up MFA) emit a
        # {code, message} dict expecting the code to survive to the client.
        raise HTTPException(
            status_code=401,
            detail={"code": "mfa_required", "message": "MFA code required."},
        )

    @app.get("/stepup-extra")
    def _stepup_extra():
        raise HTTPException(
            status_code=403,
            detail={"message": "enroll first", "hint": "scan the QR"},
        )

    @app.get("/validate")
    def _validate(count: int):  # required int query param
        return {"count": count}

    install_error_handlers(app)
    # raise_server_exceptions=False so the catch-all handler runs.
    return TestClient(app, raise_server_exceptions=False)


def _assert_problem(r, *, status, code):
    assert r.status_code == status
    assert r.headers["content-type"].startswith(PROBLEM_JSON)
    body = r.json()
    assert body["status"] == status
    assert body["code"] == code
    assert body["type"] == f"urn:baselith:error:{code}"
    assert "title" in body
    assert "detail" in body
    return body


def test_item_not_found_maps_to_404(client):
    body = _assert_problem(client.get("/notfound"), status=404, code="not_found")
    assert body["error_type"] == "ItemNotFoundError"
    assert body["detail"] == "no such item"
    assert body["instance"] == "/notfound"


def test_plugin_config_maps_to_400(client):
    _assert_problem(client.get("/badconfig"), status=400, code="invalid_configuration")


def test_plugin_integrity_maps_to_403(client):
    _assert_problem(client.get("/forbidden"), status=403, code="integrity_error")


def test_insufficient_role_maps_to_403(client):
    body = _assert_problem(
        client.get("/no-role"), status=403, code="insufficient_permissions"
    )
    assert body["error_type"] == "InsufficientPermissionsError"


def test_insufficient_scope_maps_to_403(client):
    body = _assert_problem(
        client.get("/no-scope"), status=403, code="insufficient_scope"
    )
    assert body["error_type"] == "InsufficientScopeError"


def test_quota_exceeded_maps_to_429(client):
    body = _assert_problem(client.get("/over-quota"), status=429, code="quota_exceeded")
    assert body["error_type"] == "QuotaExceededError"


def test_generic_baselith_maps_to_500(client):
    _assert_problem(client.get("/generic-baselith"), status=500, code="internal_error")


def test_unhandled_exception_is_problem_and_generic(client):
    body = _assert_problem(client.get("/boom"), status=500, code="internal_error")
    assert body["error_type"] == "RuntimeError"
    # Generic detail — internals not leaked.
    assert body["detail"] == "Internal server error."
    assert "unexpected" not in client.get("/boom").text


def test_http_exception_is_problem_json_but_preserves_detail(client):
    # HTTPException is now RFC 9457, but `detail` stays top-level for back-compat.
    body = _assert_problem(client.get("/http"), status=418, code="http_error")
    assert body["detail"] == "teapot"


def test_http_exception_preserves_headers(client):
    r = client.get("/unauthorized")
    body = _assert_problem(r, status=401, code="unauthorized")
    assert body["detail"] == "nope"
    # WWW-Authenticate must survive the problem+json conversion.
    assert r.headers["WWW-Authenticate"] == "Bearer"


def test_structured_detail_promotes_machine_code(client):
    # A {code, message} detail surfaces its code as the envelope code/type and
    # its message as the human detail — so clients branch on `mfa_required`
    # instead of the generic `unauthorized`.
    body = _assert_problem(client.get("/stepup"), status=401, code="mfa_required")
    assert body["detail"] == "MFA code required."


def test_structured_detail_defaults_code_and_carries_extras(client):
    # No `code` in the dict → fall back to the per-status code; unknown keys ride
    # along as top-level extensions.
    body = _assert_problem(client.get("/stepup-extra"), status=403, code="forbidden")
    assert body["detail"] == "enroll first"
    assert body["hint"] == "scan the QR"


def test_validation_error_is_problem_with_errors_extension(client):
    r = client.get("/validate")  # missing required `count`
    body = _assert_problem(r, status=422, code="validation_error")
    assert isinstance(body["errors"], list)
    assert body["errors"], "per-field errors should be attached"


def test_request_id_present_in_problem(client):
    r = client.get("/boom", headers={"X-Request-ID": "test-corr-123"})
    assert "request_id" in r.json()
