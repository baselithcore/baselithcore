import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

from core.plugins.exporters.router import router as backstage_router, set_backstage_provider
from core.middleware.security import require_admin_or_job

# Create a dedicated app for testing the backstage router
app = FastAPI()
app.include_router(backstage_router)

@pytest.fixture
def client():
    # Override security dependency for testing
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

@pytest.mark.asyncio
async def test_get_software_template_success(client, tmp_path):
    """Test successful retrieval of the software template."""
    # Create a dummy template file
    template_dir = tmp_path / "templates" / "backstage"
    template_dir.mkdir(parents=True)
    template_file = template_dir / "software-template.yaml"
    template_content = "apiVersion: scout.backstage.io/v1alpha1\nkind: Template"
    template_file.write_text(template_content)

    # Patch the Path usage in the router to point to our test file
    with patch("core.plugins.exporters.router.Path", return_value=template_file):
        # We need to re-mock Path specifically for the .exists() and .read_text() calls
        # or just mock the whole Path object carefully.
        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = template_content
        
        with patch("core.plugins.exporters.router.Path", return_value=mock_path):
            response = client.get("/api/backstage/software-template.yaml")
            
            assert response.status_code == 200
            assert response.text == template_content
            assert response.headers["content-type"] == "application/x-yaml"

def test_get_software_template_not_found(client):
    """Test behavior when the template file is missing."""
    mock_path = MagicMock()
    mock_path.exists.return_value = False
    
    with patch("core.plugins.exporters.router.Path", return_value=mock_path):
        response = client.get("/api/backstage/software-template.yaml")
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]
