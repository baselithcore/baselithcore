"""QuotaMiddleware: no-op unless enabled; rejects over-quota authenticated
requests with 429. Pure-ASGI harness — no live DB, auth/quota managers mocked."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.middleware.quota as qm
from core.auth import AuthRole, AuthUser
from core.quotas.manager import QuotaExceededError, QuotaWindow


class _FakeApp:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope, receive, send) -> None:
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})


class _FakeAuth:
    def __init__(self, user) -> None:
        self._user = user

    async def authenticate(self, header):
        return self._user


class _FakeQuota:
    def __init__(self, raise_on=None) -> None:
        self.raise_on = raise_on
        self.calls = []

    async def check_and_consume_tenant(self, tid, **k):
        self.calls.append(("tenant", tid))
        if self.raise_on == "tenant":
            raise QuotaExceededError(tid, QuotaWindow.DAILY, 1, 1)

    async def check_and_consume(self, ident, **k):
        self.calls.append(("identity", ident))
        if self.raise_on == "identity":
            raise QuotaExceededError(ident, QuotaWindow.DAILY, 1, 1)


def _scope(auth=True):
    headers = [(b"authorization", b"Bearer x")] if auth else []
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": headers,
    }


async def _run(mw):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)

    await mw(_scope(), receive, send)
    return sent


def _patch(monkeypatch, *, enabled, user, quota):
    monkeypatch.setattr(
        qm, "get_quota_config", lambda: SimpleNamespace(enabled=enabled)
    )
    monkeypatch.setattr(qm, "get_auth_manager", lambda: _FakeAuth(user))
    monkeypatch.setattr(qm, "get_quota_manager", lambda: quota)


_USER = AuthUser(user_id="u1", roles={AuthRole.USER}, tenant_id="t1")


@pytest.mark.asyncio
async def test_noop_when_disabled(monkeypatch):
    app = _FakeApp()
    q = _FakeQuota()
    _patch(monkeypatch, enabled=False, user=_USER, quota=q)
    await _run(qm.QuotaMiddleware(app))
    assert app.called and q.calls == []  # never even authenticated


@pytest.mark.asyncio
async def test_passthrough_when_within_quota(monkeypatch):
    app = _FakeApp()
    q = _FakeQuota()
    _patch(monkeypatch, enabled=True, user=_USER, quota=q)
    await _run(qm.QuotaMiddleware(app))
    assert app.called
    assert ("tenant", "t1") in q.calls and ("identity", "u1") in q.calls


@pytest.mark.asyncio
async def test_429_when_tenant_quota_exceeded(monkeypatch):
    app = _FakeApp()
    q = _FakeQuota(raise_on="tenant")
    _patch(monkeypatch, enabled=True, user=_USER, quota=q)
    sent = await _run(qm.QuotaMiddleware(app))
    assert not app.called  # request blocked before the route
    assert sent[0]["status"] == 429


@pytest.mark.asyncio
async def test_anonymous_passes_through(monkeypatch):
    app = _FakeApp()
    q = _FakeQuota()
    anon = AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})
    _patch(monkeypatch, enabled=True, user=anon, quota=q)
    await _run(qm.QuotaMiddleware(app))
    assert app.called and q.calls == []  # not quota-scoped
