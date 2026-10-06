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
            "id: 1\ndata: Hello\n\n",
            "id: 2\ndata:  World\n\n",
            "id: 3\n" + chat_module.SSE_DONE_EVENT,
        ]

    def test_a_multiline_chunk_keeps_one_data_line_per_line(self) -> None:
        """SSE forbids a raw newline inside a field value (RFC: one per line)."""
        frames = _collect(chat_module.sse_stream(_StubRequest(), _chunks("a\nb"), 30.0))

        assert frames[0] == "id: 1\ndata: a\ndata: b\n\n"

    def test_the_terminal_event_is_always_emitted(self) -> None:
        frames = _collect(chat_module.sse_stream(_StubRequest(), _chunks(), 30.0))

        assert frames == ["id: 1\n" + chat_module.SSE_DONE_EVENT]
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

        assert frames[-1].endswith(chat_module.SSE_DONE_EVENT)
        # Two data frames, then the disconnect is observed and we stop pulling.
        assert sum(f.endswith("data: tok\n\n") for f in frames) == 2
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

        assert frames[0] == "id: 1\ndata: partial\n\n"
        assert frames[-2].startswith("id: 2\nevent: error\ndata: ")
        assert frames[-1] == "id: 3\n" + chat_module.SSE_DONE_EVENT

    def test_the_error_frame_leaks_no_exception_detail(self) -> None:
        async def explodes() -> AsyncIterator[str]:
            raise RuntimeError("provider read failed: token abc123")
            yield ""  # pragma: no cover - unreachable, keeps this a generator

        frames = _collect(chat_module.sse_stream(_StubRequest(), explodes(), 30.0))

        assert len(frames) == 2
        assert frames[0].startswith("id: 1\nevent: error\n")
        assert frames[1] == "id: 2\n" + chat_module.SSE_DONE_EVENT
        assert "abc123" not in "".join(frames)
        assert "RuntimeError" not in "".join(frames)


class TestTimeout:
    def test_a_hung_upstream_is_cut_off_at_the_deadline(self) -> None:
        async def hangs() -> AsyncIterator[str]:
            yield "first"
            await asyncio.sleep(30)
            yield "never"  # pragma: no cover - the deadline fires first

        frames = _collect(chat_module.sse_stream(_StubRequest(), hangs(), 0.05))

        assert frames == [
            "id: 1\ndata: first\n\n",
            "id: 2\n" + chat_module.SSE_DONE_EVENT,
        ]


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
            "id: 1\ndata: Hello\n\nid: 2\ndata:  World\n\nid: 3\n"
            + chat_module.SSE_DONE_EVENT
        )


class TestErrorPayload:
    def test_the_error_event_carries_a_json_code_and_request_id(self) -> None:
        import json

        from core.observability.setup import request_id_ctx

        async def explodes() -> AsyncIterator[str]:
            raise RuntimeError("provider read failed: token abc123")
            yield ""  # pragma: no cover - unreachable, keeps this a generator

        async def run() -> list[str]:
            token = request_id_ctx.set("req-42")
            try:
                return [
                    f
                    async for f in chat_module.sse_stream(
                        _StubRequest(), explodes(), 30.0
                    )
                ]
            finally:
                request_id_ctx.reset(token)

        frames = asyncio.run(run())
        data_line = next(
            line for line in frames[0].split("\n") if line.startswith("data: ")
        )
        payload = json.loads(data_line[len("data: ") :])
        assert payload == {
            "code": "stream_failed",
            "detail": "stream failed",
            "request_id": "req-42",
        }
        assert "abc123" not in frames[0]


class TestHeartbeat:
    def test_a_quiet_source_gets_keepalive_comments_without_losing_the_chunk(
        self,
    ) -> None:
        async def slow() -> AsyncIterator[str]:
            await asyncio.sleep(0.12)
            yield "late"

        frames = _collect(
            chat_module.sse_stream(
                _StubRequest(), slow(), 30.0, heartbeat_interval=0.03
            )
        )

        keepalives = [f for f in frames if f == ": keepalive\n\n"]
        assert len(keepalives) >= 2
        # The pending read survived every heartbeat: the chunk still arrives.
        assert "id: 1\ndata: late\n\n" in frames
        assert frames[-1] == "id: 2\n" + chat_module.SSE_DONE_EVENT

    def test_a_client_gone_during_a_quiet_period_closes_the_source(self) -> None:
        closed = asyncio.Event()

        async def silent() -> AsyncIterator[str]:
            try:
                await asyncio.sleep(30)
                yield "never"  # pragma: no cover
            finally:
                closed.set()

        async def run() -> list[str]:
            frames = [
                f
                async for f in chat_module.sse_stream(
                    _StubRequest(disconnect_after=2),
                    silent(),
                    30.0,
                    heartbeat_interval=0.01,
                )
            ]
            return frames

        frames = asyncio.run(run())
        assert frames[-1].endswith(chat_module.SSE_DONE_EVENT)
        assert "data: never" not in "".join(frames)
        # The pending read was cancelled and the generator closed, not leaked.
        assert closed.is_set()

    def test_the_interval_comes_from_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        import plugins.api_routers._sse as sse_module

        monkeypatch.setattr(
            sse_module,
            "get_app_config",
            lambda: SimpleNamespace(sse_heartbeat_seconds=4.5),
        )
        assert sse_module.heartbeat_seconds() == 4.5
        monkeypatch.setattr(sse_module, "get_app_config", lambda: SimpleNamespace())
        assert sse_module.heartbeat_seconds() == sse_module.DEFAULT_HEARTBEAT_SECONDS
