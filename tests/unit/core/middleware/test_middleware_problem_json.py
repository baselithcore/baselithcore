"""Every short-circuit a middleware answers is an RFC 9457 problem document.

Cost-control 429s, plugin-activation 503s, CSRF 403s and the idempotency
400/409/422 refusals used to answer ad-hoc ``{"detail": ...}`` or
``{"error", "message"}`` bodies as ``application/json`` with no correlation
id, so a client needed a special case per layer. They now share the shape of
every other API error: ``application/problem+json`` with ``type``, ``status``,
``code`` and ``request_id``.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

import orjson
import pytest

from core.middleware._idempotency_replay import (
    StoredResponse,
    idempotency_problem,
    replay_entry,
    request_target_digest,
)
from core.middleware.cost_control import (
    BudgetExceededError,
    CostController,
    CostControlMiddleware,
)
from core.middleware.csrf import CSRFOriginMiddleware
from core.middleware.observability import RequestIdMiddleware
from core.middleware.plugin_activation import PluginActivationMiddleware
from core.plugins._activation_backoff import PluginActivationBackoffError

PROBLEM = b"application/problem+json"


async def _drive(
    app: Any,
    *,
    method: str = "POST",
    path: str = "/x",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
    extra_scope: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[bytes, bytes], dict[str, Any]]:
    """Run ``app`` behind RequestIdMiddleware; return (start, headers, json)."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    scope.update(extra_scope or {})
    sent: list[dict[str, Any]] = []
    messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await RequestIdMiddleware(app)(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    raw = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    return start, dict(start["headers"]), orjson.loads(raw)


def _assert_problem(
    start: dict[str, Any],
    headers: dict[bytes, bytes],
    body: dict[str, Any],
    status: int,
) -> None:
    assert start["status"] == status
    assert headers[b"content-type"].startswith(PROBLEM)
    assert body["status"] == status
    assert body["type"] == f"urn:baselith:error:{body['code']}"
    # The body carries the same correlation id the response header echoes.
    assert body["request_id"] == headers[b"x-request-id"].decode()


# --- cost control -------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_control_429_is_problem_json_without_thresholds() -> None:
    async def app(scope, receive, send):
        raise BudgetExceededError("Token limit exceeded: 12000/10000")

    start, headers, body = await _drive(
        CostControlMiddleware(app, controller=CostController())
    )
    _assert_problem(start, headers, body, 429)
    assert body["code"] == "budget_exceeded"
    assert body["instance"] == "/x"
    assert "12000" not in body["detail"]
    assert b"retry-after" not in headers


@pytest.mark.asyncio
async def test_cost_control_429_keeps_a_retry_after_hint() -> None:
    error = BudgetExceededError("over")
    error.retry_after = 30  # type: ignore[attr-defined]

    async def app(scope, receive, send):
        raise error

    start, headers, body = await _drive(
        CostControlMiddleware(app, controller=CostController())
    )
    _assert_problem(start, headers, body, 429)
    assert headers[b"retry-after"] == b"30"


# --- plugin activation --------------------------------------------------------


class _Registry:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    def match_plugin_route(self, path: str) -> str:
        return "demo"

    async def ensure_plugin_active(self, name: str) -> bool:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return bool(self._outcome)


async def _never(scope, receive, send):  # pragma: no cover - must not run
    raise AssertionError("downstream must not be reached")


@pytest.mark.parametrize(
    ("outcome", "retry_after"),
    [
        (False, True),
        (RuntimeError("not ready"), False),
        (PluginActivationBackoffError("demo", 7), True),
    ],
)
@pytest.mark.asyncio
async def test_plugin_activation_503_is_problem_json(
    outcome: Any, retry_after: bool
) -> None:
    app_state = SimpleNamespace(
        state=SimpleNamespace(plugin_registry=_Registry(outcome))
    )
    start, headers, body = await _drive(
        PluginActivationMiddleware(_never),
        method="GET",
        path="/demo/x",
        extra_scope={"app": app_state},
    )
    _assert_problem(start, headers, body, 503)
    assert body["code"] == "plugin_unavailable"
    assert (b"retry-after" in headers) is retry_after


# --- CSRF ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csrf_403_is_problem_json() -> None:
    middleware = CSRFOriginMiddleware(_never, allow_origins=["https://app.example"])
    start, headers, body = await _drive(
        middleware, headers={"origin": "https://evil.example"}
    )
    _assert_problem(start, headers, body, 403)
    assert body["code"] == "csrf_origin_rejected"


# --- idempotency --------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "idempotency_key_invalid"), (409, "idempotency_key_in_flight")],
)
@pytest.mark.asyncio
async def test_idempotency_refusals_are_problem_json(status: int, code: str) -> None:
    async def app(scope, receive, send):
        await idempotency_problem(scope, receive, send, status, code, "nope")

    start, headers, body = await _drive(app)
    _assert_problem(start, headers, body, status)
    assert body["code"] == code
    assert body["detail"] == "nope"


@pytest.mark.asyncio
async def test_idempotency_target_mismatch_422_is_problem_json() -> None:
    entry = StoredResponse(
        status=200, headers=[], body=b"{}", body_sha256=None, target_sha256="0" * 64
    )

    async def app(scope, receive, send):
        await replay_entry(entry, "", scope, receive, send)

    start, headers, body = await _drive(app)
    _assert_problem(start, headers, body, 422)
    assert body["code"] == "idempotency_key_mismatch"


@pytest.mark.asyncio
async def test_idempotency_body_mismatch_422_is_problem_json() -> None:
    target = {"path": "/x", "query_string": b""}
    entry = StoredResponse(
        status=200,
        headers=[],
        body=b"{}",
        body_sha256=hashlib.sha256(b"original").hexdigest(),
        target_sha256=request_target_digest(target),
    )

    async def app(scope, receive, send):
        await replay_entry(entry, "", scope, receive, send)

    start, headers, body = await _drive(app, body=b"different")
    _assert_problem(start, headers, body, 422)
    assert body["code"] == "idempotency_key_mismatch"
