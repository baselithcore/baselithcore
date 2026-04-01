import pytest
from unittest.mock import AsyncMock, MagicMock
import importlib
from fastapi.testclient import TestClient
from core.models.chat import ChatRequest

# We need to mock the rate limiter since it requires redis which might not be running
from fastapi_limiter.depends import RateLimiter


@pytest.fixture
def chat_router_module():
    import core.chat.service as chat_service_module
    import core.routers.chat as chat_router_module

    # Ensure no previous test leaves behind a resolved global chat service.
    chat_service_module._chat_service = None
    return importlib.reload(chat_router_module)


@pytest.fixture
def client(chat_router_module):
    from fastapi import FastAPI

    async def mock_require_user():
        return {"id": "test_user", "tenant_id": "test_tenant"}

    app = FastAPI()
    app.include_router(chat_router_module.router)
    app.dependency_overrides[chat_router_module.require_user] = mock_require_user

    # Bypass RateLimiter by overriding the specific instance used in the router
    for route in chat_router_module.router.routes:
        if hasattr(route, "dependencies"):
            for dep in route.dependencies:
                if isinstance(dep.dependency, RateLimiter):
                    app.dependency_overrides[dep.dependency] = lambda: None

    with TestClient(app) as client:
        yield client


@pytest.fixture
def mock_chat_service(chat_router_module, monkeypatch):
    service = MagicMock()
    service.handle_chat_async = AsyncMock()
    service.handle_chat_stream_async = AsyncMock()
    monkeypatch.setattr(chat_router_module, "chat_service", service)
    return service


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
