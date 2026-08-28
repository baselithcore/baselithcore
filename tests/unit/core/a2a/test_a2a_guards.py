"""Perimeter guards on the A2A JSON-RPC endpoint (core.a2a.guards).

The dispatcher's only authentication is an optional HMAC signature, and
unsigned requests are allowed outright outside production — so the per-source-IP
rate limit and the request-body cap are what stand between an open port and
unmetered agent invocation.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.a2a import guards  # noqa: E402
from core.a2a.agent_card import AgentCard  # noqa: E402
from core.a2a.guards import (  # noqa: E402
    DEFAULT_A2A_MAX_BODY_BYTES,
    DEFAULT_A2A_RATE_LIMIT_PER_MINUTE,
    A2ARateLimitGuard,
    a2a_max_body_bytes,
    a2a_rate_limit_per_minute,
)
from core.a2a.router import create_a2a_router  # noqa: E402
from core.a2a.server import EchoA2AServer  # noqa: E402

PAYLOAD = (
    b'{"jsonrpc": "2.0", "method": "message/send", "id": "1", '
    b'"params": {"message": {"role": "user", '
    b'"parts": [{"kind": "text", "text": "hi"}], "messageId": "m1"}}}'
)


class _CountingLimiter:
    """Stand-in for ``core.middleware.rate_limiter.RateLimiter``.

    Same contract (429 ``HTTPException`` with ``Retry-After``), deterministic
    and free of Redis so the wiring can be asserted exactly.
    """

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.calls: list[tuple[str, int, int]] = []

    async def check(self, identifier: str, limit: int, window_seconds: int) -> None:
        self.calls.append((identifier, limit, window_seconds))
        if len(self.calls) > self.budget:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded, please try again shortly.",
                headers={"Retry-After": "42", "RateLimit-Limit": str(limit)},
            )


class _UnavailableLimiter:
    """Fail-closed backend: 503 instead of silently widening the limit."""

    async def check(self, identifier: str, limit: int, window_seconds: int) -> None:
        raise HTTPException(status_code=503, detail="backend down")


def _client(monkeypatch: pytest.MonkeyPatch, limiter=None) -> TestClient:
    """Router wired with an injected limiter (one guard per router)."""
    if limiter is not None:
        monkeypatch.setattr(
            "core.a2a.router.A2ARateLimitGuard",
            lambda: A2ARateLimitGuard(limiter=limiter),
        )
    card = AgentCard(name="echo", description="echo agent")
    app = FastAPI()
    app.include_router(create_a2a_router(EchoA2AServer(card)))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _unsigned_dev_mode(monkeypatch: pytest.MonkeyPatch):
    """Baseline posture these guards exist for: unsigned, non-production."""
    monkeypatch.delenv("BASELITH_A2A_SHARED_SECRET", raising=False)
    monkeypatch.delenv("BASELITH_A2A_RATE_LIMIT_PER_MINUTE", raising=False)
    monkeypatch.delenv("BASELITH_A2A_MAX_BODY_BYTES", raising=False)
    monkeypatch.setenv("APP_ENV", "development")


class TestConfig:
    def test_defaults(self) -> None:
        assert a2a_rate_limit_per_minute() == DEFAULT_A2A_RATE_LIMIT_PER_MINUTE
        assert a2a_max_body_bytes() == DEFAULT_A2A_MAX_BODY_BYTES

    def test_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BASELITH_A2A_RATE_LIMIT_PER_MINUTE", "5")
        monkeypatch.setenv("BASELITH_A2A_MAX_BODY_BYTES", "2048")
        assert a2a_rate_limit_per_minute() == 5
        assert a2a_max_body_bytes() == 2048

    def test_garbage_env_falls_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_A2A_RATE_LIMIT_PER_MINUTE", "many")
        assert a2a_rate_limit_per_minute() == DEFAULT_A2A_RATE_LIMIT_PER_MINUTE

    def test_negative_is_treated_as_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_A2A_RATE_LIMIT_PER_MINUTE", "-1")
        assert a2a_rate_limit_per_minute() == 0


class TestRateLimit:
    def test_budget_exhaustion_returns_jsonrpc_429(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        limiter = _CountingLimiter(budget=2)
        client = _client(monkeypatch, limiter)

        for _ in range(2):
            assert client.post("/a2a", content=PAYLOAD).status_code == 200

        blocked = client.post("/a2a", content=PAYLOAD)
        assert blocked.status_code == 429
        body = blocked.json()
        assert body["jsonrpc"] == "2.0"
        assert body["error"]["code"] == guards.A2A_ERROR_RATE_LIMITED
        # The limiter's Retry-After survives onto the JSON-RPC error.
        assert blocked.headers["retry-after"] == "42"

    def test_bucket_key_and_budget_follow_the_security_manager_pattern(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        limiter = _CountingLimiter(budget=10)
        _client(monkeypatch, limiter).post("/a2a", content=PAYLOAD)

        identifier, limit, window = limiter.calls[0]
        assert identifier.startswith("a2a:")  # "<scope>:<source ip>"
        assert limit == DEFAULT_A2A_RATE_LIMIT_PER_MINUTE
        assert window == guards.A2A_RATE_LIMIT_WINDOW_SECONDS

    def test_env_budget_is_passed_to_the_limiter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_A2A_RATE_LIMIT_PER_MINUTE", "3")
        limiter = _CountingLimiter(budget=10)
        _client(monkeypatch, limiter).post("/a2a", content=PAYLOAD)
        assert limiter.calls[0][1] == 3

    def test_zero_disables_the_limiter_entirely(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_A2A_RATE_LIMIT_PER_MINUTE", "0")
        limiter = _CountingLimiter(budget=0)
        resp = _client(monkeypatch, limiter).post("/a2a", content=PAYLOAD)
        assert resp.status_code == 200
        assert limiter.calls == []

    def test_signed_requests_are_metered_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Throttling runs before signature verification, so a flood of
        bogus-but-signed requests cannot burn unbounded HMAC work."""
        monkeypatch.setenv("BASELITH_A2A_SHARED_SECRET", "mesh-secret")
        limiter = _CountingLimiter(budget=0)
        resp = _client(monkeypatch, limiter).post("/a2a", content=PAYLOAD)
        assert resp.status_code == 429
        assert len(limiter.calls) == 1

    def test_fail_closed_backend_rejects_with_503(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resp = _client(monkeypatch, _UnavailableLimiter()).post("/a2a", content=PAYLOAD)
        assert resp.status_code == 503
        assert resp.json()["error"]["code"] == guards.A2A_ERROR_AGENT_UNAVAILABLE

    def test_unbuildable_limiter_does_not_break_the_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        guard = A2ARateLimitGuard()
        monkeypatch.setattr(
            guard,
            "_get_limiter",
            lambda: None,  # e.g. no redis package / unreadable config
        )
        monkeypatch.setattr("core.a2a.router.A2ARateLimitGuard", lambda: guard)
        card = AgentCard(name="echo", description="echo agent")
        app = FastAPI()
        app.include_router(create_a2a_router(EchoA2AServer(card)))
        assert TestClient(app).post("/a2a", content=PAYLOAD).status_code == 200

    async def test_real_rate_limiter_trips_on_its_in_memory_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end against the shared middleware limiter (Redis-less path).

        Exercises the actual ``RateLimiter`` the SecurityManager uses, forced
        onto its in-memory fixed window so the test needs no Redis.
        """
        from core.middleware.rate_limiter import RateLimiter

        monkeypatch.setenv("BASELITH_A2A_RATE_LIMIT_PER_MINUTE", "2")
        limiter = RateLimiter()
        limiter._redis = None
        limiter._fail_mode = "open"
        guard = A2ARateLimitGuard(limiter=limiter)

        class _Req:
            client = type("C", (), {"host": "203.0.113.9"})()

        statuses = []
        for _ in range(4):
            blocked = await guard.check(_Req())
            statuses.append(200 if blocked is None else blocked.status_code)

        assert statuses == [200, 200, 429, 429]


class TestBodyCap:
    def test_oversized_body_is_refused_before_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_A2A_MAX_BODY_BYTES", "128")
        resp = _client(monkeypatch, _CountingLimiter(budget=10)).post(
            "/a2a", content=b"x" * 2048
        )
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == guards.A2A_ERROR_PAYLOAD_TOO_LARGE

    def test_chunked_body_without_content_length_is_still_capped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BASELITH_A2A_MAX_BODY_BYTES", "128")

        def _chunks():
            for _ in range(8):
                yield b"x" * 64

        resp = _client(monkeypatch, _CountingLimiter(budget=10)).post(
            "/a2a", content=_chunks()
        )
        assert resp.status_code == 413

    def test_normal_payload_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        resp = _client(monkeypatch, _CountingLimiter(budget=10)).post(
            "/a2a", content=PAYLOAD
        )
        assert resp.status_code == 200
        assert "result" in resp.json()

    def test_zero_disables_the_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BASELITH_A2A_MAX_BODY_BYTES", "0")
        resp = _client(monkeypatch, _CountingLimiter(budget=10)).post(
            "/a2a", content=PAYLOAD
        )
        assert resp.status_code == 200
