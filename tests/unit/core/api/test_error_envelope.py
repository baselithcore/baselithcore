"""Tests for the standardized error envelope handlers."""

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

    @app.get("/generic-baselith")
    def _gb():
        raise BaselithError("root failure")

    @app.get("/boom")
    def _boom():
        raise RuntimeError("unexpected")

    @app.get("/http")
    def _http():
        raise HTTPException(status_code=418, detail="teapot")

    install_error_handlers(app)
    # raise_server_exceptions=False so the catch-all handler runs.
    return TestClient(app, raise_server_exceptions=False)


def test_item_not_found_maps_to_404(client):
    r = client.get("/notfound")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found"
    assert err["type"] == "ItemNotFoundError"
    assert err["message"] == "no such item"


def test_plugin_config_maps_to_400(client):
    r = client.get("/badconfig")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_configuration"


def test_plugin_integrity_maps_to_403(client):
    r = client.get("/forbidden")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "integrity_error"


def test_insufficient_role_maps_to_403(client):
    r = client.get("/no-role")
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "insufficient_permissions"
    assert err["type"] == "InsufficientPermissionsError"


def test_insufficient_scope_maps_to_403(client):
    r = client.get("/no-scope")
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "insufficient_scope"
    assert err["type"] == "InsufficientScopeError"


def test_generic_baselith_maps_to_500(client):
    r = client.get("/generic-baselith")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


def test_unhandled_exception_is_enveloped_and_generic(client):
    r = client.get("/boom")
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "internal_error"
    assert err["type"] == "RuntimeError"
    # Generic message — internals not leaked.
    assert err["message"] == "Internal server error."
    assert "unexpected" not in r.text


def test_http_exception_is_NOT_enveloped(client):
    # Existing FastAPI behaviour preserved: {"detail": ...}, not the envelope.
    r = client.get("/http")
    assert r.status_code == 418
    assert r.json() == {"detail": "teapot"}


def test_request_id_present_in_envelope(client):
    r = client.get("/boom", headers={"X-Request-ID": "test-corr-123"})
    assert "request_id" in r.json()["error"]
