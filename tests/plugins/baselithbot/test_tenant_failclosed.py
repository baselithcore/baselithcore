"""Security regression: baselithbot tenant resolution must FAIL CLOSED.

`tenant_from_request` must never collapse "auth failed / absent" into a real
tenant id. It returns "default" ONLY when no central auth subsystem exists
(single-tenant deployment); when central auth IS active it returns the real
tenant on success and ``None`` (callers refuse data) on any failure.
"""

from __future__ import annotations

import pytest

import plugins.baselithbot.control.tenant as tn


class _Req:
    def __init__(self, header: str | None = None) -> None:
        self.headers = {"authorization": header} if header else {}


class _User:
    def __init__(self, tenant_id: str, authenticated: bool = True) -> None:
        self.tenant_id = tenant_id
        self._authenticated = authenticated

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated


class _Manager:
    def __init__(self, mode: str) -> None:
        self._mode = mode

    async def authenticate(self, header: str):
        if self._mode == "raise":
            raise RuntimeError("transient verify error")
        if self._mode == "anon":
            return _User("default", authenticated=False)
        return _User("tenant-real")


def _set_manager(monkeypatch, manager):
    monkeypatch.setattr(tn, "_central_auth_manager", lambda: manager)


@pytest.mark.asyncio
async def test_no_central_auth_is_single_tenant_default(monkeypatch):
    _set_manager(monkeypatch, None)
    assert await tn.tenant_from_request(_Req("Bearer x")) == "default"


@pytest.mark.asyncio
async def test_central_auth_missing_bearer_fails_closed(monkeypatch):
    _set_manager(monkeypatch, _Manager("ok"))
    assert await tn.tenant_from_request(_Req()) is None


@pytest.mark.asyncio
async def test_central_auth_verify_error_fails_closed(monkeypatch):
    _set_manager(monkeypatch, _Manager("raise"))
    assert await tn.tenant_from_request(_Req("Bearer bad")) is None


@pytest.mark.asyncio
async def test_central_auth_anonymous_fails_closed(monkeypatch):
    _set_manager(monkeypatch, _Manager("anon"))
    assert await tn.tenant_from_request(_Req("Bearer x")) is None


@pytest.mark.asyncio
async def test_central_auth_valid_returns_real_tenant(monkeypatch):
    _set_manager(monkeypatch, _Manager("ok"))
    assert await tn.tenant_from_request(_Req("Bearer good")) == "tenant-real"
