import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from baselith.core.models.chat import ChatRequest, ChatResponse


@pytest.fixture
def chat_router_module():
    import baselith.core.chat.service as chat_service_module
    import baselith.core.routers.chat as chat_router_module

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
    # The service returns a ChatResponse (its declared type); the body is
    # that model's JSON, exactly as before the route declared response_model.
    mock_response = ChatResponse(answer="Hello!", sources=[])
    mock_chat_service.handle_chat_async = AsyncMock(return_value=mock_response)

    payload = {"query": "Hello", "conversation_id": "123"}

    # Execute
    response = client.post("/chat", json=payload)

    # Verify
    assert response.status_code == 200
    assert response.json() == mock_response.model_dump(mode="json")

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


def test_chat_stream_endpoint(client, chat_router_module, mock_chat_service):
    """The stream is SSE: one ``data:`` frame per chunk, then ``event: done``.

    It answered ``text/plain`` with the chunks concatenated, so a client had no
    frame boundaries and no way to distinguish "finished" from "disconnected".
    """

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
    assert response.text == (
        "id: 1\ndata: Hello\n\nid: 2\ndata:  World\n\nid: 3\n"
        + chat_router_module.SSE_DONE_EVENT
    )
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["x-accel-buffering"] == "no"

    # Verify mock call
    mock_chat_service.handle_chat_stream_async.assert_called_once()
    call_args = mock_chat_service.handle_chat_stream_async.call_args[0][0]
    assert call_args.query == "Hello Stream"


def _posted_run_ids(client, mock_chat_service, requests, user_ids):
    """POST each (headers) in ``requests`` as the matching identity.

    ``None`` is a request the auth layer attached no identity to (the
    auth-disabled anonymous bypass), ``"anonymous"`` an anonymous AuthUser,
    anything else an authenticated user of that id. Returns the run ids.
    """
    from fastapi import Request

    from baselith.core.auth.types import AuthRole, AuthUser

    mock_chat_service.handle_chat_async = AsyncMock(
        return_value=ChatResponse(answer="ok", sources=[])
    )
    # The client fixture overrides exactly one dependency: require_user.
    (require_user,) = list(client.app.dependency_overrides)
    for headers, user_id in zip(requests, user_ids, strict=True):

        async def as_identity(request: Request, user_id=user_id) -> str:
            if user_id == "anonymous":
                request.state.user = AuthUser(
                    user_id="anonymous", roles={AuthRole.ANONYMOUS}
                )
            elif user_id is not None:
                request.state.user = AuthUser(user_id=user_id, tenant_id="t1")
            return user_id or "anonymous"

        client.app.dependency_overrides[require_user] = as_identity
        client.post("/chat", json={"query": "Hello"}, headers=headers)
    return [
        c.kwargs["run_id"] for c in mock_chat_service.handle_chat_async.call_args_list
    ]


def test_chat_run_id_follows_the_idempotency_key(client, mock_chat_service):
    """A retry with the same Idempotency-Key is an attempt at the same run."""
    ids = _posted_run_ids(
        client,
        mock_chat_service,
        [
            {"Idempotency-Key": "k-1"},
            {"Idempotency-Key": "k-1"},
            {"Idempotency-Key": "k-2"},
        ],
        ["alice", "alice", "alice"],
    )
    assert ids[0] == ids[1] != ids[2]
    assert all(run_id.startswith("chat-") for run_id in ids)


def test_chat_run_id_is_scoped_to_the_principal(client, mock_chat_service):
    """Two users reusing a key never share a run, nor each other's answer."""
    ids = _posted_run_ids(
        client,
        mock_chat_service,
        [{"Idempotency-Key": "k-1"}, {"Idempotency-Key": "k-1"}],
        ["alice", "bob"],
    )
    assert ids[0] != ids[1]


@pytest.mark.parametrize("user_id", [None, "anonymous"])
def test_anonymous_callers_never_get_a_replayable_run(
    client, mock_chat_service, user_id
):
    """Every anonymous caller shares one principal id: a key from them would
    let one caller read back another's recorded answer."""
    ids = _posted_run_ids(
        client,
        mock_chat_service,
        [{"Idempotency-Key": "k-1"}, {"Idempotency-Key": "k-1"}],
        [user_id, user_id],
    )
    assert ids[0] != ids[1]


def test_request_id_is_not_a_replay_key(client, mock_chat_service):
    """X-Request-ID is client-chosen and echoed in responses and logs."""
    ids = _posted_run_ids(
        client,
        mock_chat_service,
        [{"X-Request-ID": "req-1"}, {"X-Request-ID": "req-1"}],
        ["alice", "alice"],
    )
    assert ids[0] != ids[1]


def test_chat_run_lease_held_elsewhere_answers_409(client, mock_chat_service):
    from baselith.core.orchestration.checkpoint_errors import (
        CheckpointLeaseHeldError,
    )

    mock_chat_service.handle_chat_async = AsyncMock(
        side_effect=CheckpointLeaseHeldError("chat-x", "other-owner")
    )
    response = client.post(
        "/chat", json={"query": "Hello"}, headers={"Idempotency-Key": "k-1"}
    )
    assert response.status_code == 409
