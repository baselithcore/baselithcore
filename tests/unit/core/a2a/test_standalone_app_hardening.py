"""``create_standalone_app`` is a documented deployment shape, not a demo.

It faces peer agents directly without the middleware stack ``core.api.factory``
builds, so it must at least carry the two perimeter guards a bare FastAPI has
no equivalent for: a bound on the request body (an unbounded one is a trivial
memory-exhaustion vector) and baseline security headers.
"""

from __future__ import annotations

import pytest

from core.a2a import AgentCard
from core.a2a.router import create_standalone_app
from core.a2a.server import A2AServer


class _Server(A2AServer):
    async def handle_message(self, message):  # pragma: no cover - unused here
        return message


@pytest.fixture
def app():
    card = AgentCard(name="tester", description="test agent")
    return create_standalone_app(_Server(card))


def _mounted(app) -> set[str]:
    return {m.cls.__name__ for m in app.user_middleware}


def test_request_size_limit_is_mounted(app) -> None:
    assert "RequestSizeLimitMiddleware" in _mounted(app)


def test_security_headers_are_mounted(app) -> None:
    assert "SecurityHeadersMiddleware" in _mounted(app)


def test_responses_carry_security_headers(app) -> None:
    """End-to-end: the guard is not just registered, it actually fires."""
    from fastapi.testclient import TestClient

    response = TestClient(app).get("/a2a/health")
    assert response.status_code == 200
    assert "x-content-type-options" in {k.lower() for k in response.headers}


def test_security_headers_wrap_the_size_limiter(app) -> None:
    """Ordering, not just presence.

    ``add_middleware`` inserts at index 0, so the *last* registered is
    outermost. SecurityHeaders must be outside RequestSizeLimit, otherwise the
    413 the limiter short-circuits ships with no CSP/HSTS/nosniff — the exact
    response an attacker can most easily provoke.
    """
    names = [m.cls.__name__ for m in app.user_middleware]
    # user_middleware is ordered outermost-first.
    assert names.index("SecurityHeadersMiddleware") < names.index(
        "RequestSizeLimitMiddleware"
    )


def test_oversized_body_is_rejected_with_security_headers(app) -> None:
    """The end-to-end consequence of the ordering above."""
    from fastapi.testclient import TestClient

    from core.config import get_security_config

    limit = getattr(get_security_config(), "max_request_size_bytes", 10 * 1024 * 1024)
    if not limit:
        pytest.skip("request size limiting disabled in this configuration")

    response = TestClient(app).post(
        "/a2a",
        content=b"x" * (limit + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert "x-content-type-options" in {k.lower() for k in response.headers}
