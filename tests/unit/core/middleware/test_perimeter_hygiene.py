"""Small perimeter seams: anonymous rate-limit keys are /64-bucketed, rejected
origins and request paths are log-safe, and the in-memory limiter fallback
has a hard size cap."""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.middleware import csrf as csrf_module
from core.middleware import observability as obs_module
from core.middleware.observability import RequestIdMiddleware
from core.middleware.security import SecurityManager

rate_limiter_module = importlib.import_module("core.middleware.rate_limiter")


@pytest.mark.asyncio
async def test_anonymous_limit_key_collapses_an_ipv6_client_to_its_64(
    mock_security_config,
) -> None:
    """Keying on the full /128 hands an attacker 2**64 fresh budgets."""
    mock_security_config.auth_required = False
    mock_security_config.api_keys_user = set()
    mock_security_config.api_keys_admin = set()
    mock_security_config.api_keys_job = set()
    with patch("core.middleware.rate_limiter.create_redis_client") as factory:
        factory.return_value = AsyncMock()
        manager = SecurityManager(mock_security_config)
    manager.rate_limiter = AsyncMock()

    with patch("core.auth.manager.get_auth_manager") as get_auth:
        auth = AsyncMock()
        anon = MagicMock()
        anon.is_authenticated = False
        auth.authenticate.return_value = anon
        get_auth.return_value = auth
        request = MagicMock()
        request.headers = {}
        request.client.host = "2001:db8:1:2:aaaa:bbbb:cccc:dddd"
        request.state = MagicMock()
        request.state._auth_memo = None
        await manager.enforce_auth(request, allowed_roles={"user"}, limit_per_minute=10)

    key = manager.rate_limiter.check.await_args.args[0]
    assert key == "default:anonymous:2001:db8:1:2::/64"


def test_rejected_origin_is_logged_single_line(monkeypatch) -> None:
    logged: list[tuple] = []
    monkeypatch.setattr(
        csrf_module, "logger", MagicMock(warning=lambda *a, **k: logged.append(a))
    )
    mw = csrf_module.CSRFOriginMiddleware(lambda *a: None, allow_origins=["https://ok"])
    evil = b"https://evil\r\nX-Forged: 1" + b"A" * 600
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/x",
        "headers": [(b"origin", evil), (b"host", b"api.example.com")],
    }
    assert mw._rejection_reason(scope) == "origin not allowed"
    rendered = logged[0][0] % logged[0][1:]
    assert "\r" not in rendered and "\n" not in rendered
    assert len(logged[0][1]) <= 256


@pytest.mark.asyncio
async def test_bound_request_path_is_log_safe(monkeypatch) -> None:
    bound: dict = {}

    class _Ctx:
        def __init__(self, **kw):
            bound.update(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(obs_module, "bind_context", _Ctx)

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})

    path = "/x\n[error] forged" + "y" * 600
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}

    async def send(_m):
        pass

    await RequestIdMiddleware(inner)(scope, None, send)
    assert "\n" not in bound["http_path"]
    assert len(bound["http_path"]) <= 256


@pytest.mark.asyncio
async def test_fallback_limiter_map_is_hard_capped() -> None:
    rl = rate_limiter_module.RateLimiter.__new__(rate_limiter_module.RateLimiter)
    rl._fallback = {}
    rl._fallback_lock = asyncio.Lock()
    rl._fallback_checks_since_prune = 0
    cap = rate_limiter_module.RateLimiter._FALLBACK_MAX_ENTRIES
    for i in range(cap + 500):
        await rl._check_fallback(f"client-{i}", limit=100, window_seconds=60)
    assert len(rl._fallback) <= cap
    # The newest client is kept; the eviction took the oldest.
    assert f"client-{cap + 499}" in rl._fallback
    assert "client-0" not in rl._fallback
