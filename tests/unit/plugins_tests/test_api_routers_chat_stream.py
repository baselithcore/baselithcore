"""``POST /chat/stream`` is a Server-Sent Events endpoint.

It used to answer ``text/plain`` with raw concatenated tokens: no frame
boundaries, so a client could not tell one token from the next or a finished
stream from a dropped connection; no disconnect check, so a closed tab kept the
generator (and the upstream LLM call) running; and no wall-clock bound, so a
hung provider held the worker slot open forever.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plugins.api_routers.chat as chat_module


class _StubRequest:
    """Minimal stand-in for ``starlette.requests.Request.is_disconnected``."""

    def __init__(self, disconnect_after: int | None = None) -> None:
        self._disconnect_after = disconnect_after
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        if self._disconnect_after is None:
            return False
        return self.checks > self._disconnect_after


async def _chunks(*values: str) -> AsyncIterator[str]:
    for value in values:
        yield value


def _collect(agen: AsyncIterator[str]) -> list[str]:
    async def run() -> list[str]:
        return [frame async for frame in agen]

    return asyncio.run(run())


class TestSseFraming:
    def test_each_chunk_becomes_one_data_frame(self) -> None:
        request = _StubRequest()
        frames = _collect(
            chat_module.sse_stream(request, _chunks("Hello", " World"), 30.0)
        )

        assert frames == [
            "data: Hello\n\n",
            "data:  World\n\n",
            chat_module.SSE_DONE_EVENT,
        ]

    def test_a_multiline_chunk_keeps_one_data_line_per_line(self) -> None:
        """SSE forbids a raw newline inside a field value (RFC: one per line)."""
        frames = _collect(chat_module.sse_stream(_StubRequest(), _chunks("a\nb"), 30.0))

        assert frames[0] == "data: a\ndata: b\n\n"

    def test_the_terminal_event_is_always_emitted(self) -> None:
        frames = _collect(chat_module.sse_stream(_StubRequest(), _chunks(), 30.0))

        assert frames == [chat_module.SSE_DONE_EVENT]
        assert chat_module.SSE_DONE_EVENT.startswith("event: done\n")


class TestDisconnect:
    def test_a_gone_client_stops_the_generator(self) -> None:
        produced = 0

        async def forever() -> AsyncIterator[str]:
            nonlocal produced
            while True:
                produced += 1
                yield "tok"

        request = _StubRequest(disconnect_after=2)
        frames = _collect(chat_module.sse_stream(request, forever(), 30.0))

        assert frames[-1] == chat_module.SSE_DONE_EVENT
        # Two data frames, then the disconnect is observed and we stop pulling.
        assert frames.count("data: tok\n\n") == 2
        assert produced <= 3


class TestSourceFailure:
    def test_a_raising_source_still_terminates_the_stream(self) -> None:
        """A 200 that just stops is indistinguishable from a dropped socket.

        The response headers are long gone by the time the provider read fails,
        so the only way to tell the client something went wrong is in-band: an
        ``event: error`` frame, then the usual terminal ``event: done``.
        """

        async def explodes() -> AsyncIterator[str]:
            yield "partial"
            raise RuntimeError("provider read failed: token abc123")

        frames = _collect(chat_module.sse_stream(_StubRequest(), explodes(), 30.0))

        assert frames[0] == "data: partial\n\n"
        assert frames[-2:] == [chat_module.SSE_ERROR_EVENT, chat_module.SSE_DONE_EVENT]

    def test_the_error_frame_leaks_no_exception_detail(self) -> None:
        async def explodes() -> AsyncIterator[str]:
            raise RuntimeError("provider read failed: token abc123")
            yield ""  # pragma: no cover - unreachable, keeps this a generator

        frames = _collect(chat_module.sse_stream(_StubRequest(), explodes(), 30.0))

        assert frames == [chat_module.SSE_ERROR_EVENT, chat_module.SSE_DONE_EVENT]
        assert "abc123" not in "".join(frames)
        assert "RuntimeError" not in "".join(frames)


class TestTimeout:
    def test_a_hung_upstream_is_cut_off_at_the_deadline(self) -> None:
        async def hangs() -> AsyncIterator[str]:
            yield "first"
            await asyncio.sleep(30)
            yield "never"  # pragma: no cover - the deadline fires first

        frames = _collect(chat_module.sse_stream(_StubRequest(), hangs(), 0.05))

        assert frames == ["data: first\n\n", chat_module.SSE_DONE_EVENT]


class TestEndpoint:
    @pytest.fixture
    def client(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        service = MagicMock()

        async def fake_stream() -> AsyncIterator[str]:
            yield "Hello"
            yield " World"

        service.handle_chat_stream_async = AsyncMock(return_value=fake_stream())
        monkeypatch.setattr(chat_module, "chat_service", service)

        app = FastAPI()
        app.include_router(chat_module.router)
        app.dependency_overrides[chat_module.require_user] = lambda: {"id": "u"}
        with TestClient(app) as client:
            yield client

    def test_the_response_is_event_stream(self, client: Any) -> None:
        response = client.post("/chat/stream", json={"query": "hi"})

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.text == (
            "data: Hello\n\ndata:  World\n\n" + chat_module.SSE_DONE_EVENT
        )
