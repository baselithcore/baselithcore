import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI
from core.routers.admin import router as admin_router, verify_credentials
from core.routers.feedback import router as feedback_router
from core.routers.tenant import router as tenant_router
from core.services.tenant import Tenant

# Create app for testing routers
app = FastAPI()
app.include_router(admin_router)
app.include_router(feedback_router)
app.include_router(tenant_router)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mock_verify_credentials():
    app.dependency_overrides[verify_credentials] = lambda: "admin"
    yield
    app.dependency_overrides = {}


@pytest.fixture
def mock_feedback_service():
    with (
        patch("core.routers.admin.get_feedback_service") as mock_admin,
        patch("core.routers.feedback.get_feedback_service") as mock_feedback,
    ):
        service_mock = AsyncMock()
        mock_admin.return_value = service_mock
        mock_feedback.return_value = service_mock
        yield service_mock


@pytest.fixture
def mock_tenant_service():
    with patch("core.routers.tenant.get_tenant_service") as mock:
        service_mock = AsyncMock()
        mock.return_value = service_mock
        yield service_mock


def test_admin_data_endpoint(client, mock_verify_credentials, mock_feedback_service):
    """Test standard admin analytics endpoint."""
    mock_feedback_service.get_analytics.return_value = {"total": 100}

    response = client.get("/admin/data")

    assert response.status_code == 200
    assert response.json() == {"total": 100}
    mock_feedback_service.get_analytics.assert_called_once()


def test_feedback_submission(client, mock_feedback_service):
    """Test submitting feedback."""
    payload = {
        "query": "hello",
        "answer": "world",
        "feedback": "positive",
        "conversation_id": "123",
    }

    response = client.post("/feedback", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    mock_feedback_service.insert_feedback.assert_called_once()


def test_list_feedbacks(client, mock_feedback_service):
    """Test listing feedbacks."""
    mock_feedback_service.get_feedbacks.return_value = [
        {"id": 1, "feedback": "positive"}
    ]

    response = client.get("/feedbacks")

    assert response.status_code == 200
    assert len(response.json()) == 1


def test_list_tenants(client, mock_verify_credentials, mock_tenant_service):
    """Test listing tenants."""
    mock_tenant_service.list_tenants.return_value = [
        Tenant(id="t1", name="Tenant 1", status="active", created_at="2024-01-01")
    ]

    response = client.get("/admin/tenants")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == "t1"


def test_create_tenant_success(client, mock_verify_credentials, mock_tenant_service):
    """Test creating a tenant successfully."""
    mock_tenant_service.get_tenant.return_value = None
    mock_tenant_service.create_tenant.return_value = Tenant(
        id="new", name="New Tenant", status="active", created_at="2024-01-01"
    )

    payload = {"id": "new", "name": "New Tenant"}
    response = client.post("/admin/tenants", json=payload)

    assert response.status_code == 201
    assert response.json()["id"] == "new"


def test_create_tenant_conflict(client, mock_verify_credentials, mock_tenant_service):
    """Test creating a tenant that already exists."""
    mock_tenant_service.get_tenant.return_value = Tenant(
        id="existing", name="Existing", status="active", created_at="2024-01-01"
    )

    payload = {"id": "existing", "name": "Existing"}
    response = client.post("/admin/tenants", json=payload)

    assert response.status_code == 409
