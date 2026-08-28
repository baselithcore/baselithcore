"""Admission gate for the MCP Streamable HTTP transport: capability + metering.

Split from ``test_http_transport.py`` to keep both modules under the 500-line
cap; the transport-protocol tests stay there.
"""

from types import SimpleNamespace

from .test_http_transport import (
    _app,
    _asgi_client,
    _config,
    _initialize_msg,
    _StubAuthManager,
    _user,
)


async def test_scoped_key_without_mcp_scope_is_refused(monkeypatch):
    """Authenticating is not authorizing: a least-privilege key minted for an
    unrelated resource used to reach the whole tool catalog and tools/call."""
    import core.auth.manager as auth_manager_module

    scoped = _user("key-1", scopes=("webhooks:write",))
    monkeypatch.setattr(
        auth_manager_module, "get_auth_manager", lambda: _StubAuthManager(scoped)
    )
    config = _config(mcp_http_require_auth=True, mcp_http_required_scope="mcp:invoke")
    async with _asgi_client(_app(config)) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Authorization": "Bearer k"},
        )

        assert response.status_code == 403
        assert response.json()["error"]["code"] == -32002


async def test_identity_without_capability_api_is_refused(monkeypatch):
    """An identity type that cannot answer a scope question is refused rather
    than granted the whole surface by default."""
    import core.auth.manager as auth_manager_module

    legacy = SimpleNamespace(user_id="legacy", is_authenticated=True)
    monkeypatch.setattr(
        auth_manager_module, "get_auth_manager", lambda: _StubAuthManager(legacy)
    )
    config = _config(mcp_http_require_auth=True, mcp_http_required_scope="mcp:invoke")
    async with _asgi_client(_app(config)) as client:
        response = await client.post(
            "/mcp", json=_initialize_msg(), headers={"Authorization": "Bearer k"}
        )

        assert response.status_code == 403


async def test_empty_required_scope_disables_the_capability_check(monkeypatch):
    import core.auth.manager as auth_manager_module

    scoped = _user("key-1", scopes=("webhooks:write",))
    monkeypatch.setattr(
        auth_manager_module, "get_auth_manager", lambda: _StubAuthManager(scoped)
    )
    config = _config(mcp_http_require_auth=True, mcp_http_required_scope="")
    async with _asgi_client(_app(config)) as client:
        response = await client.post(
            "/mcp", json=_initialize_msg(), headers={"Authorization": "Bearer k"}
        )

        assert response.status_code == 200


async def test_requests_are_metered_per_identity(monkeypatch):
    """Every request spawns server-side work, so an authenticated caller must
    not be able to flood the endpoint unmetered."""
    from core.mcp import http_authz

    metered: list[tuple[str, int, int]] = []

    class _Limiter:
        async def check(self, key, limit, window):
            metered.append((key, limit, window))

    monkeypatch.setattr(http_authz, "_rate_limiter", _Limiter())
    config = _config(mcp_http_rate_limit_per_minute=120)
    async with _asgi_client(_app(config)) as client:
        await client.post("/mcp", json=_initialize_msg())

    assert metered and metered[0][1:] == (120, 60)
    http_authz.reset_rate_limiter()


async def test_over_budget_request_gets_jsonrpc_429(monkeypatch):
    from fastapi import HTTPException

    from core.mcp import http_authz

    class _Limiter:
        async def check(self, key, limit, window):
            raise HTTPException(status_code=429, headers={"Retry-After": "60"})

    monkeypatch.setattr(http_authz, "_rate_limiter", _Limiter())
    config = _config(mcp_http_rate_limit_per_minute=1)
    async with _asgi_client(_app(config)) as client:
        response = await client.post("/mcp", json=_initialize_msg())

    assert response.status_code == 429
    assert response.json()["error"]["code"] == -32003
    assert response.headers["Retry-After"] == "60"
    http_authz.reset_rate_limiter()


async def test_sessions_are_owner_scoped_when_auth_is_disabled():
    """With auth off every caller used to share one ``None`` owner bucket, so
    any client could ride or terminate another's session."""
    from core.mcp.http_authz import build_gate

    config = _config(mcp_http_require_auth=False)
    gate = build_gate(config, "/mcp", frozenset())

    request = SimpleNamespace(
        headers={},
        client=SimpleNamespace(host="10.0.0.7"),
    )
    owner, rejection = await gate(request)

    assert rejection is None
    assert owner == "10.0.0.7"
