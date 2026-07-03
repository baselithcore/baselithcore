from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from core.api.factory import create_app


def test_app_startup_with_marketplace():
    """Verify the FastAPI app starts with the marketplace integration."""
    try:
        app = create_app()
        assert app is not None
    except Exception as e:
        pytest.fail(f"App failed to start: {e}")


def test_marketplace_cli_check():
    """Verify marketplace CLI check function returns a boolean."""
    from core.cli.commands.plugin.marketplace import _check_marketplace

    result = _check_marketplace()
    assert isinstance(result, bool)


def test_trusted_host_middleware_blocks_unlisted_hosts(monkeypatch):
    from core.api import factory as factory_module

    monkeypatch.setattr(
        factory_module,
        "get_security_config",
        lambda: SimpleNamespace(allow_origins=[], trusted_hosts=["allowed.local"]),
    )

    app = create_app()

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    app.router.lifespan_context = _noop_lifespan

    with TestClient(app) as client:
        allowed = client.get("/health", headers={"host": "allowed.local"})
        blocked = client.get("/health", headers={"host": "blocked.local"})

    assert allowed.status_code == 200
    assert blocked.status_code == 400
