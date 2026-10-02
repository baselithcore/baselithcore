"""RLS behind a transaction-mode pooler is refused unless accepted explicitly.

``app.tenant_id`` is bound with ``set_config(..., false)`` — session scope —
and memoized per pooled connection. Behind PgBouncer in transaction pooling
mode the next statement may run on a different backend that still carries
another tenant's GUC, so the policies isolate nothing. ``DB_PREPARED_STATEMENTS=false``
is the one signal the configuration already carries for "transaction pooler";
with RLS on it is a conflict the boot must refuse, not a warning.
"""

from __future__ import annotations

import pytest

from core.config.storage import StorageConfig
from core.db import connection as db_connection
from core.db.rls_posture import RlsBypassError, enforce_rls_posture


def _cfg(**overrides: object) -> StorageConfig:
    base: dict[str, object] = {
        "DB_RLS_ENABLED": True,
        "DB_PREPARED_STATEMENTS": False,
        "POSTGRES_ENABLED": True,
    }
    return StorageConfig(**{**base, **overrides})  # type: ignore[arg-type]


def test_rls_with_transaction_pooler_is_a_conflict() -> None:
    problem = _cfg().rls_pooler_conflict()
    assert problem is not None
    assert "transaction" in problem.lower()
    assert "DB_RLS_ALLOW_TRANSACTION_POOLER" in problem


def test_explicit_acceptance_clears_the_conflict() -> None:
    assert _cfg(DB_RLS_ALLOW_TRANSACTION_POOLER=True).rls_pooler_conflict() is None


def test_no_conflict_without_rls_or_with_session_pooling() -> None:
    assert _cfg(DB_RLS_ENABLED=False).rls_pooler_conflict() is None
    assert _cfg(DB_PREPARED_STATEMENTS=True).rls_pooler_conflict() is None


def test_acceptance_defaults_to_false() -> None:
    assert StorageConfig().db_rls_allow_transaction_pooler is False


async def test_startup_posture_check_refuses_the_conflict_in_every_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.config as config_pkg

    monkeypatch.setattr(config_pkg, "get_storage_config", lambda: _cfg())
    monkeypatch.setenv("APP_ENV", "development")
    with pytest.raises(RlsBypassError) as info:
        await enforce_rls_posture()
    assert "transaction" in str(info.value).lower()


def test_pool_creation_refuses_the_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db_connection, "_storage_config", _cfg())
    monkeypatch.setattr(db_connection, "_POOL", None)
    monkeypatch.setattr(db_connection, "_ASYNC_POOL", None)
    monkeypatch.setattr(db_connection, "POSTGRES_ENABLED", True)
    with pytest.raises(RuntimeError, match="transaction"):
        db_connection._get_pool()
    with pytest.raises(RuntimeError, match="transaction"):
        db_connection._get_async_pool()
