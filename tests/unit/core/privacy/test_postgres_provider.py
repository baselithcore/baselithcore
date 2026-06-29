"""Tests for the PostgreSQL DSR data provider.

The DB layer is mocked: ``get_async_cursor`` is patched to hand out a sequence
of fake cursors so each query/DELETE returns controlled rows / rowcounts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest

from core.context import reset_tenant_context, set_tenant_context
from core.privacy import (
    DataProvider,
    PostgresDataProvider,
    RetentionProvider,
)


class _Cur:
    """Fake async cursor recording executes and returning canned data."""

    def __init__(self, *, rows=None, rowcount=0, raise_on_execute=False):
        self.rows = rows or []
        self.rowcount = rowcount
        self.raise_on_execute = raise_on_execute
        self.calls: list[tuple] = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if self.raise_on_execute:
            raise RuntimeError('relation "chat_feedback" does not exist')

    async def fetchall(self):
        return self.rows


class _Ctx:
    def __init__(self, cur: _Cur):
        self._cur = cur

    async def __aenter__(self) -> _Cur:
        return self._cur

    async def __aexit__(self, *exc) -> bool:
        return False


def _patch(monkeypatch, cursors: list[_Cur]):
    """Patch get_async_cursor to yield ``cursors`` in order; expose them."""
    seq = list(cursors)
    used: list[_Cur] = []

    def _make(*_args, **_kwargs):
        cur = seq.pop(0)
        used.append(cur)
        return _Ctx(cur)

    monkeypatch.setattr("core.privacy.postgres.get_async_cursor", _make)
    return used


def test_satisfies_protocols():
    p = PostgresDataProvider()
    assert p.name == "postgres"
    assert isinstance(p, DataProvider)
    assert isinstance(p, RetentionProvider)


class TestExport:
    @pytest.mark.asyncio
    async def test_serialises_uuid_and_datetime(self, monkeypatch):
        iid = UUID("11111111-1111-1111-1111-111111111111")
        fid = UUID("22222222-2222-2222-2222-222222222222")
        ts = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        cursors = _patch(
            monkeypatch,
            [
                _Cur(
                    rows=[
                        {
                            "id": iid,
                            "user_id": "u1",
                            "timestamp": ts,
                            "metadata": {"k": "v"},
                        }
                    ]
                ),
                _Cur(rows=[{"id": fid, "interaction_id": iid, "timestamp": ts}]),
            ],
        )
        bundle = await PostgresDataProvider().export("u1")

        assert bundle["interactions"][0]["id"] == str(iid)
        assert bundle["interactions"][0]["timestamp"] == ts.isoformat()
        assert bundle["interactions"][0]["metadata"] == {"k": "v"}
        assert bundle["feedback"][0]["interaction_id"] == str(iid)
        # Both queries are tenant-scoped (default tenant outside a request).
        assert cursors[0].calls[0][1] == ("u1", "default")
        assert cursors[1].calls[0][1] == ("default", "u1", "default")

    @pytest.mark.asyncio
    async def test_unknown_subject_is_empty(self, monkeypatch):
        _patch(monkeypatch, [_Cur(rows=[]), _Cur(rows=[])])
        bundle = await PostgresDataProvider().export("ghost")
        assert bundle == {"interactions": [], "feedback": []}


class TestErase:
    @pytest.mark.asyncio
    async def test_counts_and_orders_children_first(self, monkeypatch):
        cursors = _patch(monkeypatch, [_Cur(rowcount=2), _Cur(rowcount=3)])
        removed = await PostgresDataProvider().erase("u1")
        assert removed == 5
        # Feedback (child) deleted before interactions (parent) — FK-safe.
        assert "DELETE FROM feedback" in cursors[0].calls[0][0]
        assert "DELETE FROM interactions" in cursors[1].calls[0][0]
        assert cursors[1].calls[0][1] == ("u1", "default")

    @pytest.mark.asyncio
    async def test_nothing_to_erase(self, monkeypatch):
        _patch(monkeypatch, [_Cur(rowcount=0), _Cur(rowcount=0)])
        assert await PostgresDataProvider().erase("ghost") == 0


class TestRetention:
    @pytest.mark.asyncio
    async def test_sums_across_tables(self, monkeypatch):
        cursors = _patch(
            monkeypatch,
            [_Cur(rowcount=1), _Cur(rowcount=2), _Cur(rowcount=4)],
        )
        removed = await PostgresDataProvider().purge_expired(3600)
        assert removed == 7
        assert "DELETE FROM feedback" in cursors[0].calls[0][0]
        assert "DELETE FROM interactions" in cursors[1].calls[0][0]
        assert "DELETE FROM chat_feedback" in cursors[2].calls[0][0]
        # Retention is global (all tenants): only the cutoff is bound, no tenant.
        assert cursors[1].calls[0][1] == (3600,)
        assert "tenant_id" not in cursors[1].calls[0][0]

    @pytest.mark.asyncio
    async def test_optional_chat_feedback_failure_isolated(self, monkeypatch):
        _patch(
            monkeypatch,
            [_Cur(rowcount=1), _Cur(rowcount=2), _Cur(raise_on_execute=True)],
        )
        # chat_feedback (optional) blows up but the sweep still reports 1 + 2.
        assert await PostgresDataProvider().purge_expired(100) == 3

    @pytest.mark.asyncio
    async def test_core_table_failure_propagates(self, monkeypatch):
        _patch(
            monkeypatch,
            [_Cur(raise_on_execute=False, rowcount=0), _Cur(raise_on_execute=True)],
        )
        # The interactions delete is not optional → error surfaces to the service.
        with pytest.raises(RuntimeError):
            await PostgresDataProvider().purge_expired(100)


class TestTenantScoping:
    @pytest.mark.asyncio
    async def test_uses_active_tenant(self, monkeypatch):
        cursors = _patch(monkeypatch, [_Cur(rowcount=0), _Cur(rowcount=0)])
        token = set_tenant_context("acme")
        try:
            await PostgresDataProvider().erase("u9")
        finally:
            reset_tenant_context(token)
        # Every parameter set carries the active tenant, never "default".
        assert cursors[1].calls[0][1] == ("u9", "acme")
