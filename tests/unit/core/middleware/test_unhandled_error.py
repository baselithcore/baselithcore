"""``UnhandledErrorMiddleware``: a 500 is rendered inside RequestId, then
propagated — never swallowed, never rendered twice."""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.middleware.observability import RequestIdMiddleware
from core.middleware.unhandled_error import UnhandledErrorMiddleware


async def _boom(scope: Any, receive: Any, send: Any) -> None:
    raise RuntimeError("kaboom")


async def _late_boom(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    raise RuntimeError("after start")


async def _drive(app: Any, headers: list[tuple[bytes, bytes]] | None = None) -> list:
    sent: list = []
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": headers or [],
        "query_string": b"",
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b""}

    async def send(message: dict) -> None:
        sent.append(message)

    with pytest.raises(RuntimeError):
        await app(scope, receive, send)
    return sent


async def test_renders_problem_json_and_reraises() -> None:
    sent = await _drive(UnhandledErrorMiddleware(_boom))
    start = sent[0]
    assert start["status"] == 500
    assert dict(start["headers"])[b"content-type"].startswith(
        b"application/problem+json"
    )
    body = json.loads(b"".join(m.get("body", b"") for m in sent[1:]))
    assert body["code"] == "internal_error"
    assert "error_type" not in body  # never fingerprint the internal stack


async def test_request_id_reaches_header_and_body() -> None:
    """Inside RequestId: the header is stamped and the member is populated —
    the two things the outermost Starlette handler cannot do."""
    app = RequestIdMiddleware(UnhandledErrorMiddleware(_boom))
    sent = await _drive(app, headers=[(b"x-request-id", b"req-500-abc")])
    headers = dict(sent[0]["headers"])
    assert headers[b"x-request-id"] == b"req-500-abc"
    body = json.loads(b"".join(m.get("body", b"") for m in sent[1:]))
    assert body["request_id"] == "req-500-abc"


async def test_failure_after_response_start_is_propagated_untouched() -> None:
    sent = await _drive(UnhandledErrorMiddleware(_late_boom))
    assert [m["type"] for m in sent] == ["http.response.start"]
    assert sent[0]["status"] == 200


async def test_non_http_scopes_pass_through() -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    await UnhandledErrorMiddleware(inner)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]


def test_a_500_is_logged_once_through_the_full_stack(monkeypatch) -> None:
    """The middleware renders and re-raises; Starlette's ``ServerErrorMiddleware``
    then calls the very same handler. One failure must be one ERROR line."""
    from unittest.mock import MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import core.api.errors as errors

    mock_logger = MagicMock()
    monkeypatch.setattr(errors, "logger", mock_logger)

    app = FastAPI()
    errors.install_error_handlers(app)
    app.add_middleware(UnhandledErrorMiddleware)

    @app.get("/boom")
    def _route() -> None:
        raise RuntimeError("kaboom")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    unhandled = [
        c
        for c in mock_logger.error.call_args_list
        if "Unhandled exception" in str(c.args[0])
    ]
    assert len(unhandled) == 1
