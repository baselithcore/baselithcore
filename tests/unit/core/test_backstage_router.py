from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.middleware.security import require_admin_or_job
from core.plugins.exporters.router import (
    router as backstage_router,
)
from core.plugins.exporters.router import (
    set_backstage_provider,
)

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
    # The endpoint threads the host app's routes through so mounted-sub-app
    # plugins can export API entities from their own OpenAPI (see .mounts).
    mock_provider.get_provider_payload.assert_awaited_once_with(
        mock_registry, routes=ANY
    )


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


def test_get_all_entities_sets_etag(client, mock_provider, mock_registry):
    """GET /entities exposes a weak ETag for conditional polling."""
    set_backstage_provider(mock_provider, mock_registry)
    mock_provider.get_provider_payload = AsyncMock(
        return_value={"type": "full", "entities": []}
    )

    response = client.get("/api/backstage/entities")

    assert response.status_code == 200
    assert response.headers.get("etag", "").startswith('W/"')


def test_get_all_entities_304_on_matching_etag(client, mock_provider, mock_registry):
    """A matching If-None-Match yields 304 with no body re-serialisation."""
    set_backstage_provider(mock_provider, mock_registry)
    mock_provider.get_provider_payload = AsyncMock(
        return_value={"type": "full", "entities": []}
    )

    first = client.get("/api/backstage/entities")
    etag = first.headers["etag"]

    second = client.get("/api/backstage/entities", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.headers["etag"] == etag

    # A changed payload must produce a different ETag → 200 again.
    mock_provider.get_provider_payload = AsyncMock(
        return_value={"type": "full", "entities": [{"kind": "Component"}]}
    )
    third = client.get("/api/backstage/entities", headers={"If-None-Match": etag})
    assert third.status_code == 200
    assert third.headers["etag"] != etag


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


def test_publish_github_exchange_ignores_caller_registry_url(client):
    """The GitHub-token exchange must never target a caller-supplied hub URL.

    Regression test: a job-role key could previously redirect the forwarded
    GitHub OAuth token to an arbitrary host via ``registry_url`` (SSRF +
    credential exfiltration). The exchange now always hits
    ``OFFICIAL_MARKETPLACE_URL``.
    """
    exchange_response = MagicMock()
    exchange_response.status_code = 200
    exchange_response.json.return_value = {"access_token": "jwt-123"}

    mock_http = AsyncMock()
    mock_http.post.return_value = exchange_response
    mock_http.__aenter__.return_value = mock_http

    plugin_config = MagicMock()
    plugin_config.OFFICIAL_MARKETPLACE_URL = "https://marketplace.example.com"
    plugin_config.publish_workspace_root = None

    with (
        patch("httpx.AsyncClient", return_value=mock_http),
        patch("core.config.get_plugin_config", return_value=plugin_config),
        patch("core.plugins.exporters.router.PluginPublisher") as mock_publisher_cls,
    ):
        mock_publisher_cls.return_value.publish = AsyncMock(
            return_value={"status": "ok"}
        )
        response = client.post(
            "/api/backstage/publish",
            json={
                "plugin_path": "/srv/plugins/demo",
                "github_token": "gho_secret",
                "registry_url": "http://169.254.169.254/attacker",
            },
        )

    assert response.status_code == 200
    posted_url = mock_http.post.call_args[0][0]
    assert posted_url == "https://marketplace.example.com/auth/github/exchange"


def test_publish_rejects_path_outside_workspace_root(client):
    """PLUGIN_PUBLISH_WORKSPACE_ROOT confines packaging to the workspace."""
    from pathlib import Path

    plugin_config = MagicMock()
    plugin_config.publish_workspace_root = Path("/srv/scaffolder-workspace")

    with patch("core.config.get_plugin_config", return_value=plugin_config):
        response = client.post(
            "/api/backstage/publish",
            json={
                "plugin_path": "/srv/scaffolder-workspace/../../etc",
                "auth_token": "jwt-123",
            },
        )

    assert response.status_code == 403


def test_publish_allows_path_inside_workspace_root(client):
    from pathlib import Path

    plugin_config = MagicMock()
    plugin_config.publish_workspace_root = Path("/srv/scaffolder-workspace")

    with (
        patch("core.config.get_plugin_config", return_value=plugin_config),
        patch("core.plugins.exporters.router.PluginPublisher") as mock_publisher_cls,
    ):
        mock_publisher_cls.return_value.publish = AsyncMock(
            return_value={"status": "ok"}
        )
        response = client.post(
            "/api/backstage/publish",
            json={
                "plugin_path": "/srv/scaffolder-workspace/demo",
                "auth_token": "jwt-123",
            },
        )

    assert response.status_code == 200
