"""Tests for the additive /v1 API versioning aliases."""

import importlib

import pytest

from core.api.factory import create_app


@pytest.fixture(autouse=True)
def _fresh_router_modules():
    """Rebuild the data-router modules from source before each test.

    ``create_app()`` mounts module-level router singletons from
    ``plugins.api_routers`` (via the ``core.routers`` shims). Another test in the
    same xdist worker can leave those singletons in a mutated state (e.g.
    ``test_chat`` reloads its module), which under some orderings produced an app
    missing routes. Reloading from source here makes these assertions
    order-independent without changing app behaviour.
    """
    import core.routers.chat as chat_mod
    import core.routers.console as console_mod
    import core.routers.index as index_mod
    import core.routers.metrics as metrics_mod
    import core.routers.status as status_mod

    for mod in (status_mod, chat_mod, index_mod, metrics_mod, console_mod):
        importlib.reload(mod)
    yield


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
