"""QuotaMiddleware: no-op unless enabled; rejects over-quota authenticated
requests with 429. Pure-ASGI harness — no live DB, auth/quota managers mocked."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.middleware._auth_memo as auth_memo
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

    async def check_and_consume_pair(self, ident, tid, **k):
        # Tenant first mirrors the manager's batched check order.
        self.calls.append(("tenant", tid))
        if self.raise_on == "tenant":
            raise QuotaExceededError(tid, QuotaWindow.DAILY, 1, 1)
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


async def _run(mw, scope=None):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(msg):
        sent.append(msg)

    await mw(scope if scope is not None else _scope(), receive, send)
    return sent


def _patch(monkeypatch, *, enabled, user, quota):
    monkeypatch.setattr(
        qm, "get_quota_config", lambda: SimpleNamespace(enabled=enabled)
    )
    # Credential verification is shared with the tenant middleware and the
    # route dependency; patch it where it lives.
    monkeypatch.setattr(auth_memo, "auth_manager", lambda: _FakeAuth(user))
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


class _StrictHeaderAuth:
    """Authenticates only the exact header it expects, so a test can prove the
    middleware synthesized the right credential string."""

    def __init__(self, user, expected: str) -> None:
        self._user = user
        self._expected = expected

    async def authenticate(self, header):
        if header != self._expected:
            raise AssertionError(f"unexpected header: {header!r}")
        return self._user


def _scope_apikey():
    return {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [(b"x-api-key", b"sk_live_123")],
    }


@pytest.mark.asyncio
async def test_api_key_caller_is_quota_scoped(monkeypatch):
    """An X-API-Key caller (no Authorization header) must be metered like any
    other authenticated caller — not silently bypass QUOTAS_ENABLED. The
    middleware synthesizes ``ApiKey <key>`` to match the route dependency."""
    app = _FakeApp()
    q = _FakeQuota()
    monkeypatch.setattr(qm, "get_quota_config", lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(
        auth_memo,
        "auth_manager",
        lambda: _StrictHeaderAuth(_USER, "ApiKey sk_live_123"),
    )
    monkeypatch.setattr(qm, "get_quota_manager", lambda: q)

    await _run(qm.QuotaMiddleware(app), _scope_apikey())
    assert ("tenant", "t1") in q.calls and ("identity", "u1") in q.calls


@pytest.mark.asyncio
async def test_anonymous_passes_through(monkeypatch):
    app = _FakeApp()
    q = _FakeQuota()
    anon = AuthUser(user_id="anonymous", roles={AuthRole.ANONYMOUS})
    _patch(monkeypatch, enabled=True, user=anon, quota=q)
    await _run(qm.QuotaMiddleware(app))
    assert app.called and q.calls == []  # not quota-scoped


def test_probe_and_docs_paths_skip_quota_auth(monkeypatch):
    """Liveness probes, docs and metrics scrapes must not pay a full JWT
    verification per hit when quotas are on."""
    from core.middleware.quota import QuotaMiddleware

    assert "/health" in QuotaMiddleware._EXEMPT_PATHS
    assert "/metrics" in QuotaMiddleware._EXEMPT_PATHS
    assert "/docs" in QuotaMiddleware._EXEMPT_PATHS


class _RefundingQuota(_FakeQuota):
    def __init__(self) -> None:
        super().__init__()
        self.refunds: list[tuple[str, str, object]] = []
        self.consumed_at: object = None

    async def check_and_consume_pair(self, ident, tid, **k):
        self.consumed_at = k.get("now")
        await super().check_and_consume_pair(ident, tid, **k)

    async def refund_pair(self, ident, tid, *, now=None, **k):
        self.refunds.append((ident, tid, now))


class _StatusApp:
    def __init__(self, status: int) -> None:
        self.status = status

    async def __call__(self, scope, receive, send) -> None:
        await send({"type": "http.response.start", "status": self.status})
        await send({"type": "http.response.body", "body": b"", "more_body": False})


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404, 405, 429, 503])
async def test_unit_is_refunded_when_no_work_was_done(monkeypatch, status):
    """A request admitted by quota but answered by an inner guard (or not
    routed at all) must not spend the caller's budget."""
    q = _RefundingQuota()
    _patch(monkeypatch, enabled=True, user=_USER, quota=q)
    sent = await _run(qm.QuotaMiddleware(_StatusApp(status)))
    assert sent[0]["status"] == status
    # Refunded against the same instant it was consumed at (same period keys).
    assert q.refunds == [("u1", "t1", q.consumed_at)]
    assert q.consumed_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 201, 400, 422, 500])
async def test_unit_stays_spent_when_work_may_have_been_done(monkeypatch, status):
    q = _RefundingQuota()
    _patch(monkeypatch, enabled=True, user=_USER, quota=q)
    await _run(qm.QuotaMiddleware(_StatusApp(status)))
    assert q.refunds == []


@pytest.mark.asyncio
async def test_refund_failure_never_breaks_the_response(monkeypatch):
    class _Broken(_RefundingQuota):
        async def refund_pair(self, *a, **k):
            raise ConnectionError("redis down")

    q = _Broken()
    _patch(monkeypatch, enabled=True, user=_USER, quota=q)
    sent = await _run(qm.QuotaMiddleware(_StatusApp(429)))
    assert sent[0]["status"] == 429
