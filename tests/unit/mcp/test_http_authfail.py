"""MCP HTTP admission: failed-credential throttle and unpinned-resource warning.

* A presented credential that fails authentication is charged to the same
  per-IP ``authfail:<ip>`` window ``SecurityManager.enforce_auth`` uses, so the
  MCP endpoint is not an unmetered 401 oracle for credential stuffing.
* Mounting the transport with neither ``MCP_RESOURCE_URL`` nor
  ``TRUSTED_HOSTS`` logs an ERROR: the token audience would then follow the
  caller-controlled ``Host`` header.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from .test_http_transport import (
    _app,
    _asgi_client,
    _config,
    _initialize_msg,
    _StubAuthManager,
)

_ANONYMOUS = SimpleNamespace(user_id="anonymous", is_authenticated=False)


class _RecordingLimiter:
    """Stand-in for the SecurityManager rate limiter; trips after ``budget``."""

    def __init__(self, budget: int) -> None:
        self.budget = budget
        self.calls: list[tuple[str, int, int]] = []

    async def check(self, key: str, limit: int, window: int) -> None:
        self.calls.append((key, limit, window))
        if len(self.calls) > self.budget:
            raise HTTPException(status_code=429, headers={"Retry-After": "60"})


@pytest.fixture
def limiter(monkeypatch):
    import core.auth.manager as auth_manager_module
    import core.middleware.security as security_module

    recorder = _RecordingLimiter(budget=2)
    manager = SimpleNamespace(
        rate_limiter=recorder,
        config=SimpleNamespace(
            auth_failure_limit_per_minute=7, rate_limit_window_seconds=60
        ),
    )
    monkeypatch.setattr(security_module, "get_security_manager", lambda: manager)
    monkeypatch.setattr(
        auth_manager_module, "get_auth_manager", lambda: _StubAuthManager(_ANONYMOUS)
    )
    return recorder


async def test_failed_credentials_are_charged_to_the_authfail_window(limiter):
    config = _config(mcp_http_require_auth=True)
    async with _asgi_client(_app(config)) as client:
        statuses = [
            (
                await client.post(
                    "/mcp",
                    json=_initialize_msg(),
                    headers={"Authorization": "Bearer wrong"},
                )
            ).status_code
            for _ in range(3)
        ]

    assert statuses == [401, 401, 429]
    key, limit, window = limiter.calls[0]
    assert key.startswith("authfail:")
    assert (limit, window) == (7, 60)


async def test_throttled_response_is_a_jsonrpc_429_with_retry_after(limiter):
    limiter.budget = 0
    config = _config(mcp_http_require_auth=True)
    async with _asgi_client(_app(config)) as client:
        response = await client.post(
            "/mcp", json=_initialize_msg(), headers={"Authorization": "Bearer x"}
        )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == -32003
    assert response.headers["Retry-After"] == "60"


async def test_discovery_request_without_credentials_is_not_charged(limiter):
    """The spec's first, unauthenticated request fetches the 401 challenge."""
    limiter.budget = 0
    config = _config(mcp_http_require_auth=True)
    async with _asgi_client(_app(config)) as client:
        response = await client.post("/mcp", json=_initialize_msg())

    assert response.status_code == 401
    assert "resource_metadata" in response.headers["WWW-Authenticate"]
    assert limiter.calls == []


@pytest.mark.parametrize(
    ("resource_url", "trusted_hosts", "expected"),
    [
        ("", [], True),
        ("https://api.example.com/mcp", [], False),
        ("", ["api.example.com"], False),
    ],
)
def test_resource_unpinned(resource_url, trusted_hosts, expected):
    from core.mcp.http_authz import resource_unpinned

    cfg = SimpleNamespace(mcp_resource_url=resource_url)
    assert resource_unpinned(cfg, trusted_hosts) is expected


def test_mount_logs_error_when_resource_is_unpinned(monkeypatch):
    from core.mcp import http_authz

    errors: list[str] = []
    monkeypatch.setattr(
        http_authz.logger, "error", lambda event, **_: errors.append(event)
    )
    monkeypatch.setattr(
        "core.config.get_security_config",
        lambda: SimpleNamespace(trusted_hosts=[]),
    )

    _app(_config(mcp_resource_url=""))
    assert errors == ["mcp_http_resource_unpinned"]

    errors.clear()
    _app(_config(mcp_resource_url="https://api.example.com/mcp"))
    assert errors == []
