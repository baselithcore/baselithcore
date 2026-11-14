"""Opt-in RLS session binding: every checkout sets the `app.tenant_id` GUC so
RLS policies can isolate rows at the database. Verifies the binding is correct
and degrades safely outside a request context (never raises)."""

from __future__ import annotations

import baselith.core.db.connection as conn
from baselith.core.context import (
    TenantContextError,
    reset_tenant_context,
    set_tenant_context,
)


class _FakeCursor:
    def __init__(self, calls):
        self._calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._calls.append((sql, params))


class _FakeConn:
    def __init__(self):
        self.calls = []

    def cursor(self):
        return _FakeCursor(self.calls)


def test_rls_disabled_by_default():
    """Default OFF → enabling RLS is an explicit opt-in, never a surprise."""
    assert conn.DB_RLS_ENABLED is False


def test_apply_tenant_sets_guc_from_context():
    token = set_tenant_context("t-acme")
    try:
        c = _FakeConn()
        conn._sync_apply_tenant(c)
    finally:
        reset_tenant_context(token)
    sql, params = c.calls[0]
    assert "set_config('app.tenant_id'" in sql
    assert params == ("t-acme",)


def test_apply_tenant_degrades_to_default_on_context_error(monkeypatch):
    """A background task with no tenant (strict isolation raises) must not break
    the connection — the GUC falls back to 'default'."""

    def _raise():
        raise TenantContextError("no context")

    monkeypatch.setattr("baselith.core.context.get_current_tenant_id", _raise)
    c = _FakeConn()
    conn._sync_apply_tenant(c)
    assert c.calls[0][1] == ("default",)
