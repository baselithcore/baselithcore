import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.plugins.exporters.router import (
    router as backstage_router,
    set_backstage_provider,
)
from core.middleware.security import require_admin_or_job

# Create a dedicated app for testing the backstage router
app = FastAPI()
app.include_router(backstage_router)


@pytest.fixture
def client():
    # Override the security dependency for testing
    app.dependency_overrides[require_admin_or_job] = lambda: "admin"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides = {}


@pytest.fixture
def mock_provider():
    return MagicMock()


@pytest.fixture
def mock_registry():
    return MagicMock()


def test_get_backstage_health(client, mock_provider, mock_registry):
    """Test the health check endpoint."""
    set_backstage_provider(mock_provider, mock_registry)
    mock_registry.get_all.return_value = []

    response = client.get("/api/backstage/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["exporter"] == "BackstageProvider"


def test_get_backstage_health_reports_plugin_count(
    client, mock_provider, mock_registry
):
    """Health endpoint counts registered plugins."""
    set_backstage_provider(mock_provider, mock_registry)
    mock_registry.get_all.return_value = [MagicMock(), MagicMock()]

    response = client.get("/api/backstage/health")

    assert response.status_code == 200
    assert response.json()["registered_plugins"] == 2


# ── /api/backstage/entities ───────────────────────────────────────────────────


def test_get_all_entities_returns_provider_payload(
    client, mock_provider, mock_registry
):
    """GET /entities returns the full Entity Provider payload."""
    set_backstage_provider(mock_provider, mock_registry)
    mock_provider.get_provider_payload = AsyncMock(
        return_value={"type": "full", "entities": []}
    )

    response = client.get("/api/backstage/entities")

    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "full"
    assert data["entities"] == []
    mock_provider.get_provider_payload.assert_awaited_once_with(mock_registry)


def test_get_all_entities_includes_plugins(client, mock_provider, mock_registry):
    """GET /entities payload includes one dict per plugin."""
    set_backstage_provider(mock_provider, mock_registry)
    entity = {"apiVersion": "backstage.io/v1alpha1", "kind": "Component"}
    mock_provider.get_provider_payload = AsyncMock(
        return_value={"type": "full", "entities": [entity]}
    )

    response = client.get("/api/backstage/entities")

    assert response.status_code == 200
    assert len(response.json()["entities"]) == 1


# ── /api/backstage/entities/{plugin_name} ────────────────────────────────────


def test_get_entity_found(client, mock_provider, mock_registry):
    """GET /entities/{plugin_name} returns the catalog-info entity."""
    set_backstage_provider(mock_provider, mock_registry)
    plugin = MagicMock()
    mock_registry.get.return_value = plugin
    entity = {
        "apiVersion": "backstage.io/v1alpha1",
        "kind": "Component",
        "metadata": {"name": "my-plugin"},
    }
    mock_provider.export_entity = AsyncMock(return_value=entity)

    response = client.get("/api/backstage/entities/my-plugin")

    assert response.status_code == 200
    assert response.json()["metadata"]["name"] == "my-plugin"
    mock_registry.get.assert_called_once_with("my-plugin")
    mock_provider.export_entity.assert_awaited_once_with(plugin)


def test_get_entity_not_found(client, mock_provider, mock_registry):
    """GET /entities/{plugin_name} returns 404 for unknown plugins."""
    set_backstage_provider(mock_provider, mock_registry)
    mock_registry.get.return_value = None

    response = client.get("/api/backstage/entities/nonexistent")

    assert response.status_code == 404
    assert "nonexistent" in response.json()["detail"]


# ── /api/backstage/entities/{plugin_name}/patterns ───────────────────────────


def test_get_plugin_patterns_found(client, mock_provider, mock_registry):
    """GET /entities/{plugin_name}/patterns returns detected pattern labels."""
    set_backstage_provider(mock_provider, mock_registry)
    plugin = MagicMock()
    mock_registry.get.return_value = plugin
    mock_provider.detect_agentic_patterns = AsyncMock(
        return_value=["baselith.ai/pattern-reasoning", "baselith.ai/pattern-planning"]
    )

    response = client.get("/api/backstage/entities/my-plugin/patterns")

    assert response.status_code == 200
    patterns = response.json()
    assert "baselith.ai/pattern-reasoning" in patterns
    assert "baselith.ai/pattern-planning" in patterns
    mock_provider.detect_agentic_patterns.assert_awaited_once_with(plugin)


def test_get_plugin_patterns_not_found(client, mock_provider, mock_registry):
    """GET /entities/{plugin_name}/patterns returns 404 for unknown plugins."""
    set_backstage_provider(mock_provider, mock_registry)
    mock_registry.get.return_value = None

    response = client.get("/api/backstage/entities/ghost/patterns")

    assert response.status_code == 404
    assert "ghost" in response.json()["detail"]


def test_get_plugin_patterns_empty(client, mock_provider, mock_registry):
    """GET /entities/{plugin_name}/patterns returns empty list when no patterns detected."""
    set_backstage_provider(mock_provider, mock_registry)
    mock_registry.get.return_value = MagicMock()
    mock_provider.detect_agentic_patterns = AsyncMock(return_value=[])

    response = client.get("/api/backstage/entities/bare-plugin/patterns")

    assert response.status_code == 200
    assert response.json() == []


# ── /api/backstage/software-template.yaml ────────────────────────────────────


@pytest.mark.asyncio
async def test_get_software_template_success(client):
    """Test successful retrieval of the software template."""
    template_content = "apiVersion: scout.backstage.io/v1alpha1\nkind: Template"
    mock_path = MagicMock()
    mock_path.exists.return_value = True
    mock_path.read_text.return_value = template_content

    with patch("core.plugins.exporters.router._TEMPLATE_PATH", mock_path):
        response = client.get("/api/backstage/software-template.yaml")

        assert response.status_code == 200
        assert response.text == template_content
        assert response.headers["content-type"] == "application/x-yaml"


def test_get_software_template_not_found(client):
    """Test behavior when the template file is missing."""
    mock_path = MagicMock()
    mock_path.exists.return_value = False

    with patch("core.plugins.exporters.router._TEMPLATE_PATH", mock_path):
        response = client.get("/api/backstage/software-template.yaml")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


# ── Auth / security ───────────────────────────────────────────────────────────


def test_entities_always_enforces_auth(mock_provider, mock_registry):
    """GET /entities requires admin/job credentials — no debug bypass."""
    set_backstage_provider(mock_provider, mock_registry)

    unauthed_app = FastAPI()
    unauthed_app.include_router(backstage_router)

    # No dependency override — auth dependency runs normally and rejects the request
    with TestClient(unauthed_app, raise_server_exceptions=False) as c:
        response = c.get("/api/backstage/entities")
        assert response.status_code in (401, 403)


def test_entities_succeeds_with_valid_auth(mock_provider, mock_registry):
    """GET /entities returns 200 when require_admin_or_job is satisfied."""
    set_backstage_provider(mock_provider, mock_registry)
    mock_registry.get_all.return_value = []
    mock_provider.get_provider_payload = AsyncMock(
        return_value={"type": "full", "entities": []}
    )

    authed_app = FastAPI()
    authed_app.include_router(backstage_router)
    authed_app.dependency_overrides[require_admin_or_job] = lambda: "admin"

    with TestClient(authed_app) as c:
        assert c.get("/api/backstage/entities").status_code == 200
