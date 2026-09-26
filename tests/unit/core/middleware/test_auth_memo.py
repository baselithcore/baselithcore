"""The shared per-request credential memo: what it verifies, and what it skips."""

from __future__ import annotations

from typing import Any

import pytest

import core.middleware._auth_memo as auth_memo
from core.auth import AuthManager, AuthRole, AuthUser
from core.di.container import ServiceRegistry

_USER = AuthUser(user_id="u1", roles={AuthRole.USER}, tenant_id="t1")


class _CountingAuth:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def authenticate(self, header: str) -> AuthUser:
        self.calls.append(header)
        return _USER


def _scope(path: str = "/admin", header: bytes = b"Basic dXNlcjpwYXNz") -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(b"authorization", header)],
    }


@pytest.mark.asyncio
async def test_basic_scheme_is_not_sent_to_the_auth_manager(monkeypatch) -> None:
    """Basic (admin/metrics) is verified by the route itself: the memo must not
    run AuthManager.authenticate on it — which refused it with a WARNING per
    request."""
    auth = _CountingAuth()
    monkeypatch.setattr(auth_memo, "auth_manager", lambda: auth)

    assert await auth_memo.resolve_user(_scope()) is None
    assert auth.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header", [b"Bearer tok", b"bearer tok", b"ApiKey k", b"apikey k"]
)
async def test_bearer_and_apikey_are_still_verified(monkeypatch, header) -> None:
    auth = _CountingAuth()
    monkeypatch.setattr(auth_memo, "auth_manager", lambda: auth)

    assert await auth_memo.resolve_user(_scope("/x", header)) is _USER
    assert auth.calls == [header.decode()]


@pytest.mark.asyncio
async def test_x_api_key_is_verified(monkeypatch) -> None:
    auth = _CountingAuth()
    monkeypatch.setattr(auth_memo, "auth_manager", lambda: auth)
    scope = {"type": "http", "path": "/x", "headers": [(b"x-api-key", b"k")]}

    assert await auth_memo.resolve_user(scope) is _USER
    assert auth.calls == ["ApiKey k"]


@pytest.mark.parametrize("path", ["/metrics", "/v1/metrics", "/v1/health"])
def test_versioned_probe_aliases_are_exempt(path: str) -> None:
    assert path in auth_memo.EXEMPT_PATHS


def test_unregistered_manager_does_not_raise_through_the_registry(
    monkeypatch,
) -> None:
    """``ServiceRegistry.get`` used to raise and be caught on every call because
    the AuthManager is never registered; the fallback must not touch it."""

    get_calls: list[Any] = []

    def _get(interface: Any) -> Any:
        get_calls.append(interface)
        raise LookupError("not registered")

    sentinel = object()
    monkeypatch.setattr(ServiceRegistry, "has", classmethod(lambda cls, i: False))
    monkeypatch.setattr(ServiceRegistry, "get", classmethod(lambda cls, i: _get(i)))
    monkeypatch.setattr(auth_memo, "get_auth_manager", lambda: sentinel)

    assert auth_memo.auth_manager() is sentinel
    assert get_calls == []


def test_registered_manager_wins_and_a_reset_is_seen(monkeypatch) -> None:
    registered = object()
    global_one = object()
    monkeypatch.setattr(auth_memo, "get_auth_manager", lambda: global_one)
    ServiceRegistry.register(AuthManager, registered)
    try:
        assert auth_memo.auth_manager() is registered
    finally:
        ServiceRegistry._services.pop(AuthManager, None)
    # No stale cache: the unregistration is honoured on the next call.
    assert auth_memo.auth_manager() is global_one
