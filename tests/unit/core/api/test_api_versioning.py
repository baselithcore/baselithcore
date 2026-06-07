"""Tests for the additive /v1 API versioning aliases."""

from core.api.factory import create_app


def _paths(app) -> set[str]:
    return {getattr(r, "path", "") for r in app.routes}


def test_v1_aliases_present_by_default():
    app = create_app()
    paths = _paths(app)
    v1_paths = {p for p in paths if p.startswith("/v1/")}
    # At least the status health route should be mirrored under /v1.
    assert any(p == "/v1/health" for p in v1_paths), sorted(v1_paths)[:20]


def test_unprefixed_routes_still_present():
    # Versioning is additive — original paths must remain.
    app = create_app()
    paths = _paths(app)
    assert "/health" in paths


def test_v1_can_be_disabled(monkeypatch):
    monkeypatch.setenv("API_V1_ENABLED", "false")
    app = create_app()
    paths = _paths(app)
    assert not any(p.startswith("/v1/") for p in paths)
    # Unprefixed still there.
    assert "/health" in paths
