"""The connection pools must validate a connection before lending it out.

Without `check`, a database restart or failover leaves every pooled connection
dead and the pool keeps handing them out: each one fails on first use with
``AdminShutdown: terminating connection due to administrator command``. That is
a burst of 500s during exactly the maintenance window that was supposed to be
transparent — and nothing in the pool notices until the query raises.

These tests pin the wiring rather than the behaviour: psycopg is mocked in the
default unit run, so what is worth protecting is that the pools are still
*constructed* with a check, and that the knob to turn it off still works.
"""

from __future__ import annotations

from typing import Any

import pytest

import core.db.connection as connection

# Factory -> the name of the pool class it instantiates, and the module global
# it caches the result in.
_FACTORIES = {
    "_get_pool": ("ConnectionPool", "_POOL"),
    "_get_async_pool": ("AsyncConnectionPool", "_ASYNC_POOL"),
    "_get_replica_pool": ("ConnectionPool", "_REPLICA_POOL"),
    "_get_async_replica_pool": ("AsyncConnectionPool", "_ASYNC_REPLICA_POOL"),
}


def _build_pool_kwargs(
    monkeypatch: pytest.MonkeyPatch, factory_name: str, *, check_enabled: bool
) -> dict[str, Any] | None:
    """Call *factory_name* with the pool class stubbed; return its kwargs.

    Returns None when the factory is not present in this build (the replica
    pools only exist when a read replica is configured).
    """
    factory = getattr(connection, factory_name, None)
    if factory is None:
        return None

    pool_class_name, cache_attr = _FACTORIES[factory_name]
    captured: dict[str, Any] = {}

    class _Recorder:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        @staticmethod
        def check_connection(_conn: Any) -> None:  # pragma: no cover - marker
            return None

    monkeypatch.setattr(connection, pool_class_name, _Recorder)
    monkeypatch.setattr(connection, "DB_POOL_CHECK", check_enabled)
    monkeypatch.setattr(connection, "POSTGRES_ENABLED", True)
    # The replica factories refuse to build without DB_REPLICA_URL. Give them
    # one so they are actually exercised — a skipped test protects nothing,
    # and the replica pools go stale on a failover exactly like the primary.
    monkeypatch.setattr(
        connection, "DB_REPLICA_CONNINFO", "postgresql://replica/db", raising=False
    )
    # The factories memoise into a module global; clear it so ours is built.
    monkeypatch.setattr(connection, cache_attr, None, raising=False)

    try:
        factory()
    except RuntimeError:
        # e.g. no replica configured — nothing to assert about.
        return None
    return captured


@pytest.mark.parametrize("factory_name", sorted(_FACTORIES))
def test_pool_is_built_with_a_connection_check(
    monkeypatch: pytest.MonkeyPatch, factory_name: str
) -> None:
    kwargs = _build_pool_kwargs(monkeypatch, factory_name, check_enabled=True)
    assert kwargs is not None, f"{factory_name} did not build a pool"
    assert kwargs.get("check") is not None, (
        f"{factory_name} built a pool without `check`: after a database restart "
        "every pooled connection is dead and the pool lends it out anyway."
    )


@pytest.mark.parametrize("factory_name", sorted(_FACTORIES))
def test_check_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch, factory_name: str
) -> None:
    """DB_POOL_CHECK=false is an escape hatch, not a no-op."""
    kwargs = _build_pool_kwargs(monkeypatch, factory_name, check_enabled=False)
    assert kwargs is not None, f"{factory_name} did not build a pool"
    assert kwargs.get("check") is None
