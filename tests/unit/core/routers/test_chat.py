import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from core.routers.chat import router
from core.models.chat import ChatRequest

# We need to mock the rate limiter since it requires redis which might not be running
from fastapi_limiter.depends import RateLimiter


@pytest.fixture
def client():
    # Setup test app
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    # Mock require_user dependency since we don't want auth logic here
    # core.routers.chat depends on require_user
    # We can override it in app.dependency_overrides
    from core.middleware import require_user

    async def mock_require_user():
        return {"id": "test_user", "tenant_id": "test_tenant"}

    app.dependency_overrides[require_user] = mock_require_user

    # Bypass RateLimiter by overriding the specific instance used in the router
    # We need to find the instance which is Depends(RateLimiter(...))
    for route in router.routes:
        if hasattr(route, "dependencies"):
            for dep in route.dependencies:
                if isinstance(dep.dependency, RateLimiter):
                    app.dependency_overrides[dep.dependency] = lambda: None

    return TestClient(app)


@pytest.fixture
def mock_chat_service():
    with patch("core.routers.chat.chat_service") as mock_service:
        yield mock_service


def test_chat_endpoint_success(client, mock_chat_service):
    # Setup
    mock_response = {"answer": "Hello!", "sources": []}
    mock_chat_service.handle_chat_async = AsyncMock(return_value=mock_response)

    payload = {"query": "Hello", "conversation_id": "123"}

    # Execute
    response = client.post("/chat", json=payload)

    # Verify
    assert response.status_code == 200
    assert response.json() == mock_response

    # Verify mock call
    mock_chat_service.handle_chat_async.assert_called_once()
    call_args = mock_chat_service.handle_chat_async.call_args[0][0]
    assert isinstance(call_args, ChatRequest)
    assert call_args.query == "Hello"
    assert call_args.conversation_id == "123"


def test_chat_endpoint_validation_error(client, mock_chat_service):
    # Missing query
    payload = {"conversation_id": "123"}

    response = client.post("/chat", json=payload)

    assert response.status_code == 422
    mock_chat_service.handle_chat_async.assert_not_called()


def test_chat_stream_endpoint(client, mock_chat_service):
    # Setup
    async def fake_stream():
        yield "Hello"
        yield " World"

    mock_chat_service.handle_chat_stream_async = AsyncMock(return_value=fake_stream())

    payload = {"query": "Hello Stream"}

    # Execute
    response = client.post("/chat/stream", json=payload)

    # Verify
    assert response.status_code == 200
    assert response.text == "Hello World"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-accel-buffering"] == "no"

    # Verify mock call
    mock_chat_service.handle_chat_stream_async.assert_called_once()
    call_args = mock_chat_service.handle_chat_stream_async.call_args[0][0]
    assert call_args.query == "Hello Stream"
