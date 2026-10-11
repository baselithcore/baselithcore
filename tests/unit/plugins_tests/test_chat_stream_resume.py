"""``POST /chat/stream`` resumes a named run: replay, reset, or 409.

The service is stubbed; what is under test is the SSE layer's reading of the
attempt (``ChatStream.mode``) and of ``Last-Event-ID``, and which requests name
a stable run at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import baselith.plugins.api_routers.chat as chat_module
from baselith.core.auth.types import AuthUser
from baselith.core.orchestration.checkpoint_errors import CheckpointLeaseHeldError
from baselith.core.services.chat.stream_session import ChatStream

pytestmark = pytest.mark.unit


class _Connected:
    async def is_disconnected(self) -> bool:
        return False


async def _chunks(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


def _frames(**kwargs: Any) -> list[str]:
    async def run() -> list[str]:
        return [
            f
            async for f in chat_module.sse_stream(
                _Connected(), _chunks("a", "b", "c"), 30.0, **kwargs
            )
        ]

    return asyncio.run(run())


class TestSseResume:
    def test_skipped_events_keep_their_ids(self) -> None:
        assert _frames(skip_events=2) == [
            "id: 3\ndata: c\n\n",
            "id: 4\n" + chat_module.SSE_DONE_EVENT,
        ]

    def test_reset_opens_the_stream(self) -> None:
        frames = _frames(reset=True)
        assert frames[0] == chat_module.SSE_RESET_EVENT
        assert frames[1] == "id: 1\ndata: a\n\n"


def _client(
    monkeypatch: pytest.MonkeyPatch, outcome: Any, *, user: AuthUser | None
) -> tuple[TestClient, MagicMock]:
    service = MagicMock()
    if isinstance(outcome, BaseException):
        service.handle_chat_stream_async = AsyncMock(side_effect=outcome)
    else:
        service.handle_chat_stream_async = AsyncMock(return_value=outcome)
    monkeypatch.setattr(chat_module, "chat_service", service)

    async def as_user(request: Request) -> str:
        if user is not None:
            request.state.user = user
        return "u"

    app = FastAPI()
    app.include_router(chat_module.router)
    app.dependency_overrides[chat_module.require_user] = as_user
    return TestClient(app), service


ALICE = AuthUser(user_id="alice", tenant_id="t1")


def test_a_replayed_run_continues_after_the_last_event(monkeypatch) -> None:
    stream = ChatStream(_chunks("a", "b", "c"), run_id="chat-x", mode="replayed")
    client, service = _client(monkeypatch, stream, user=ALICE)
    response = client.post(
        "/chat/stream",
        json={"query": "q"},
        headers={"Idempotency-Key": "k-1", "Last-Event-ID": "2"},
    )
    assert response.text == "id: 3\ndata: c\n\nid: 4\n" + chat_module.SSE_DONE_EVENT
    run_id = service.handle_chat_stream_async.call_args.kwargs["run_id"]
    assert run_id is not None and run_id.startswith("chat-")


def test_a_resumed_run_tells_the_client_to_reset(monkeypatch) -> None:
    stream = ChatStream(_chunks("new"), run_id="chat-x", mode="resumed")
    client, _ = _client(monkeypatch, stream, user=ALICE)
    response = client.post(
        "/chat/stream",
        json={"query": "q"},
        headers={"Idempotency-Key": "k-1", "Last-Event-ID": "5"},
    )
    assert response.text.startswith(chat_module.SSE_RESET_EVENT + "id: 1\ndata: new")


def test_a_run_streaming_elsewhere_is_409(monkeypatch) -> None:
    client, _ = _client(monkeypatch, CheckpointLeaseHeldError("chat-x"), user=ALICE)
    response = client.post(
        "/chat/stream", json={"query": "q"}, headers={"Idempotency-Key": "k-1"}
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    ("user", "headers"),
    [
        (None, {"Idempotency-Key": "k-1"}),  # auth disabled: anonymous
        (ALICE, {}),  # no key
        (ALICE, {"X-Request-ID": "r-1"}),  # a request id is not a key
    ],
)
def test_only_an_authenticated_key_names_a_run(monkeypatch, user, headers) -> None:
    client, service = _client(monkeypatch, _chunks("x"), user=user)
    response = client.post(
        "/chat/stream", json={"query": "q"}, headers={**headers, "Last-Event-ID": "3"}
    )
    assert service.handle_chat_stream_async.call_args.kwargs["run_id"] is None
    # Nothing to replay: the client's partial text is superseded.
    assert response.text.startswith(chat_module.SSE_RESET_EVENT)


def test_a_garbled_last_event_id_is_ignored(monkeypatch) -> None:
    client, _ = _client(monkeypatch, _chunks("x"), user=ALICE)
    response = client.post(
        "/chat/stream", json={"query": "q"}, headers={"Last-Event-ID": "abc"}
    )
    assert response.text.startswith("id: 1\ndata: x")
