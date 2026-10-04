"""
Database Connection Management.

Provides synchronous and asynchronous connection pools for PostgreSQL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from psycopg import AsyncConnection, Connection, Cursor
from psycopg.rows import AsyncRowFactory, RowFactory
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from core.config import get_storage_config
from core.db._tenant_binding import (
    _async_apply_tenant,
    _current_tenant_for_session,
    _sync_apply_tenant,
    bind_tenant,
    bind_tenant_async,
)
from core.db._tracking import TrackingAsyncCursor, TrackingCursor, _track_db_query

# Re-exported for import compatibility: these used to live in this module and
# are referenced (and monkeypatched) as ``core.db.connection.<name>``.
from core.db.session_setup import (
    APP_TIMEZONE_NAME,
    SYSTEM_TENANT_ID,
    _async_apply_timezone,
    _sync_apply_timezone,
    system_tenant_scope,
)
from core.observability.logging import get_logger

__all__ = [
    "APP_TIMEZONE_NAME",
    "SYSTEM_TENANT_ID",
    "TrackingAsyncCursor",
    "TrackingCursor",
    "_async_apply_tenant",
    "_current_tenant_for_session",
    "_sync_apply_tenant",
    "_track_db_query",
    "system_tenant_scope",
]

_storage_config = get_storage_config()

POSTGRES_ENABLED = _storage_config.postgres_enabled
DB_CONNINFO = _storage_config.conninfo
DB_REPLICA_CONNINFO = _storage_config.replica_conninfo
DB_POOL_MIN_SIZE = _storage_config.db_pool_min_size
DB_POOL_MAX_SIZE = _storage_config.db_pool_max_size
DB_POOL_TIMEOUT = _storage_config.db_pool_timeout
DB_POOL_CHECK = _storage_config.db_pool_check
# Opt-in Row-Level-Security: bind the request tenant to the DB session on every
# checkout so RLS policies can isolate rows. OFF by default → the apply hook is
# skipped entirely and the connection path is byte-identical to before.
DB_RLS_ENABLED = _storage_config.db_rls_enabled
# "session" (memoized session GUC) or "transaction" (each checkout is one
# transaction, GUC transaction-local) — see core.db._tenant_binding.bind_tenant.
DB_RLS_TENANT_SCOPE = _storage_config.db_rls_tenant_scope

logger = get_logger(__name__)

_POOL: ConnectionPool | None = None
_ASYNC_POOL: AsyncConnectionPool | None = None
_POOL_OPENED: bool = False
_ASYNC_POOL_OPENED: bool = False

# Read-replica pools — created lazily only when DB_REPLICA_URL is configured.
_REPLICA_POOL: ConnectionPool | None = None
_ASYNC_REPLICA_POOL: AsyncConnectionPool | None = None
_REPLICA_POOL_OPENED: bool = False
_ASYNC_REPLICA_POOL_OPENED: bool = False


def _connection_kwargs(cursor_factory: type[Any]) -> dict[str, Any]:
    """Per-connection settings shared by every pool (primary and replica).

    ``prepare_threshold`` follows ``DB_PREPARED_STATEMENTS``: ``None`` keeps
    psycopg from preparing server-side statements, which a transaction-mode
    PgBouncer would route to a backend that never saw the ``PREPARE``.
    """
    return {
        "autocommit": True,
        "options": _storage_config.session_options,
        "cursor_factory": cursor_factory,
        "prepare_threshold": _storage_config.prepare_threshold,
    }


def _refuse_rls_pooler_conflict() -> None:
    """Refuse to build a pool whose pooling mode defeats RLS tenant binding.

    The startup posture check reports the same conflict first and more
    readably; this is the backstop for every entry point that never runs it
    (the CLI, the task-queue worker, a migration Job).
    """
    # ``getattr`` + ``isinstance``: legacy test doubles stub the storage
    # config with a bare namespace or a MagicMock.
    describe = getattr(_storage_config, "rls_pooler_conflict", None)
    problem = describe() if callable(describe) else None
    if isinstance(problem, str):
        raise RuntimeError(f"Refusing to open the database pool: {problem}")


def _get_pool() -> ConnectionPool:
    """Get or initialize the synchronous connection pool."""
    global _POOL
    if _POOL is None:
        if not POSTGRES_ENABLED:
            raise RuntimeError("PostgreSQL is disabled (POSTGRES_ENABLED=false).")
        _refuse_rls_pooler_conflict()
        _POOL = ConnectionPool(
            conninfo=DB_CONNINFO,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout=DB_POOL_TIMEOUT,
            # Hand out a connection only after checking it is still alive. A
            # database restart or failover kills every pooled connection at
            # once, and without this the pool keeps lending them out: each one
            # fails on first use with AdminShutdown, so a maintenance window
            # turns into a burst of 500s that nothing retries.
            check=ConnectionPool.check_connection if DB_POOL_CHECK else None,
            kwargs=_connection_kwargs(TrackingCursor),
            open=False,
        )
    return _POOL


def _get_async_pool() -> AsyncConnectionPool:
    """Get or initialize the asynchronous connection pool."""
    global _ASYNC_POOL
    if _ASYNC_POOL is None:
        if not POSTGRES_ENABLED:
            raise RuntimeError("PostgreSQL is disabled (POSTGRES_ENABLED=false).")
        _refuse_rls_pooler_conflict()
        _ASYNC_POOL = AsyncConnectionPool(
            conninfo=DB_CONNINFO,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout=DB_POOL_TIMEOUT,
            check=AsyncConnectionPool.check_connection if DB_POOL_CHECK else None,
            kwargs=_connection_kwargs(TrackingAsyncCursor),
            open=False,
        )
    return _ASYNC_POOL


@contextmanager
def get_connection() -> Iterator[Connection[object]]:
    """
    Returns a PostgreSQL database connection from the shared connection pool.

    Optimized: Pool is opened only once on first use, avoiding repeated check() calls.
    """
    global _POOL_OPENED

    pool = _get_pool()

    # Open pool only once on first use (thread-safe with psycopg_pool)
    if not _POOL_OPENED:
        try:
            pool.open()
            _POOL_OPENED = True
        except Exception:
            if not pool.closed:
                _POOL_OPENED = True
            else:
                raise

    with pool.connection(timeout=DB_POOL_TIMEOUT) as connection:
        _sync_apply_timezone(connection)
        with bind_tenant(connection):
            yield connection


@contextmanager
def get_cursor(
    *,
    row_factory: RowFactory[Any] | None = None,
) -> Iterator[Cursor[object]]:
    """
    Returns a ready-to-use cursor, optionally configured with a row factory.
    """

    # Branch instead of passing None through: psycopg's `cursor()` overloads
    # take a factory or nothing at all, never `row_factory=None`.
    with get_connection() as connection:
        if row_factory is None:
            with connection.cursor() as cursor:
                yield cursor
        else:
            with connection.cursor(row_factory=row_factory) as cursor:
                yield cursor


@asynccontextmanager
async def get_async_connection() -> AsyncIterator[AsyncConnection[object]]:
    """
    Returns an asynchronous PostgreSQL database connection from the shared pool.

    Optimized: Pool is opened only once on first use, avoiding repeated open() calls.
    """
    global _ASYNC_POOL_OPENED

    pool = _get_async_pool()

    # Open pool only once on first use (async-safe with psycopg_pool)
    if not _ASYNC_POOL_OPENED:
        try:
            await pool.open()
            _ASYNC_POOL_OPENED = True
        except Exception:
            if not pool.closed:
                _ASYNC_POOL_OPENED = True
            else:
                raise

    async with pool.connection(timeout=DB_POOL_TIMEOUT) as connection:
        await _async_apply_timezone(connection)
        async with bind_tenant_async(connection):
            yield connection


@asynccontextmanager
async def get_async_cursor(
    *,
    row_factory: AsyncRowFactory[Any] | None = None,
) -> AsyncIterator[Any]:
    """
    Returns an asynchronous ready-to-use cursor.
    Note: the 'Any' return annotation is used because AsyncCursor is generic.
    """
    # See get_cursor(): `row_factory=None` is not one of the overloads.
    async with get_async_connection() as connection:
        if row_factory is None:
            async with connection.cursor() as cursor:
                yield cursor
        else:
            async with connection.cursor(row_factory=row_factory) as cursor:
                yield cursor


# ---------------------------------------------------------------------------
# Read-replica routing (opt-in)
# ---------------------------------------------------------------------------
# These accessors route to a read replica (``DB_REPLICA_URL``) when configured,
# and transparently fall back to the primary pool otherwise — so existing call
# sites are unaffected and reads only move to a replica when an operator opts in
# *and* the caller explicitly uses the read API.


def _get_replica_pool() -> ConnectionPool:
    """Get or initialize the synchronous read-replica pool."""
    global _REPLICA_POOL
    if _REPLICA_POOL is None:
        if not DB_REPLICA_CONNINFO:
            raise RuntimeError("No read replica configured (DB_REPLICA_URL unset).")
        _REPLICA_POOL = ConnectionPool(
            conninfo=DB_REPLICA_CONNINFO,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout=DB_POOL_TIMEOUT,
            # Hand out a connection only after checking it is still alive. A
            # database restart or failover kills every pooled connection at
            # once, and without this the pool keeps lending them out: each one
            # fails on first use with AdminShutdown, so a maintenance window
            # turns into a burst of 500s that nothing retries.
            check=ConnectionPool.check_connection if DB_POOL_CHECK else None,
            kwargs=_connection_kwargs(TrackingCursor),
            open=False,
        )
    return _REPLICA_POOL


def _get_async_replica_pool() -> AsyncConnectionPool:
    """Get or initialize the asynchronous read-replica pool."""
    global _ASYNC_REPLICA_POOL
    if _ASYNC_REPLICA_POOL is None:
        if not DB_REPLICA_CONNINFO:
            raise RuntimeError("No read replica configured (DB_REPLICA_URL unset).")
        _ASYNC_REPLICA_POOL = AsyncConnectionPool(
            conninfo=DB_REPLICA_CONNINFO,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout=DB_POOL_TIMEOUT,
            check=AsyncConnectionPool.check_connection if DB_POOL_CHECK else None,
            kwargs=_connection_kwargs(TrackingAsyncCursor),
            open=False,
        )
    return _ASYNC_REPLICA_POOL


@contextmanager
def get_read_connection() -> Iterator[Connection[object]]:
    """Return a connection for **read-only** queries.

    Routes to the read replica when ``DB_REPLICA_URL`` is set, else falls back to
    the primary pool. Use only for queries that tolerate replica lag; never for
    writes or read-after-write within the same logical operation.
    """
    if not DB_REPLICA_CONNINFO:
        with get_connection() as connection:
            yield connection
        return

    global _REPLICA_POOL_OPENED
    pool = _get_replica_pool()
    if not _REPLICA_POOL_OPENED:
        try:
            pool.open()
            _REPLICA_POOL_OPENED = True
        except Exception:
            if not pool.closed:
                _REPLICA_POOL_OPENED = True
            else:
                raise

    with pool.connection(timeout=DB_POOL_TIMEOUT) as connection:
        _sync_apply_timezone(connection)
        with bind_tenant(connection):
            yield connection


@asynccontextmanager
async def get_async_read_connection() -> AsyncIterator[AsyncConnection[object]]:
    """Async counterpart of :func:`get_read_connection`.

    Routes to the async read-replica pool when configured, else the primary.
    """
    if not DB_REPLICA_CONNINFO:
        async with get_async_connection() as connection:
            yield connection
        return

    global _ASYNC_REPLICA_POOL_OPENED
    pool = _get_async_replica_pool()
    if not _ASYNC_REPLICA_POOL_OPENED:
        try:
            await pool.open()
            _ASYNC_REPLICA_POOL_OPENED = True
        except Exception:
            if not pool.closed:
                _ASYNC_REPLICA_POOL_OPENED = True
            else:
                raise

    async with pool.connection(timeout=DB_POOL_TIMEOUT) as connection:
        await _async_apply_timezone(connection)
        async with bind_tenant_async(connection):
            yield connection


def close_pool() -> None:
    """Explicitly closes the connection pool (useful during worker shutdown)."""
    global _POOL, _POOL_OPENED, _REPLICA_POOL, _REPLICA_POOL_OPENED
    if _POOL is not None:
        _POOL.close()
        _POOL_OPENED = False
    if _REPLICA_POOL is not None:
        _REPLICA_POOL.close()
        _REPLICA_POOL_OPENED = False


async def warm_async_pool(timeout: float = 10.0) -> bool:
    """Open the async pool at startup, waiting for ``min_size`` connections.

    Without this the first request after boot pays TCP+TLS+auth for the
    initial connections inline (cold-start tail latency). Fail-soft by
    design: a warmup failure logs a warning and returns False — the lazy
    open on first use still covers requests, and startup must not die
    because the database was briefly unreachable.

    A timed-out ``open(wait=True)`` *closes* the pool, and psycopg_pool cannot
    reopen a closed pool — so a failed warmup discards the singleton and the
    next use builds a fresh one. Keeping it would fail every later request
    with ``PoolClosed`` for the life of the process, even once the database
    is back.

    Returns:
        True when the pool is warm (or already was), False when PostgreSQL
        is disabled or the warmup attempt failed.
    """
    global _ASYNC_POOL, _ASYNC_POOL_OPENED
    if not POSTGRES_ENABLED:
        return False
    if _ASYNC_POOL_OPENED:
        return True
    try:
        pool = _get_async_pool()
        await pool.open(wait=True, timeout=timeout)
        _ASYNC_POOL_OPENED = True
        return True
    except Exception as exc:
        logger.warning("db_pool_warmup_failed error=%s", exc)
        if _ASYNC_POOL is not None and _ASYNC_POOL.closed:
            _ASYNC_POOL = None
        return False


async def close_async_pool() -> None:
    """Explicitly closes the asynchronous connection pool."""
    global _ASYNC_POOL, _ASYNC_POOL_OPENED
    global _ASYNC_REPLICA_POOL, _ASYNC_REPLICA_POOL_OPENED
    if _ASYNC_POOL is not None:
        await _ASYNC_POOL.close()
        _ASYNC_POOL_OPENED = False
    if _ASYNC_REPLICA_POOL is not None:
        await _ASYNC_REPLICA_POOL.close()
        _ASYNC_REPLICA_POOL_OPENED = False


def get_pool_stats() -> dict[str, dict[str, int]]:
    """Read-only counters for every *created* connection pool.

    Observability seam for dashboards/health surfaces. Never creates or opens
    a pool — reporting must not trigger a connection. Keys are the pool roles
    (``primary``/``primary_async``/``replica``/``replica_async``); values are
    psycopg_pool's own counters (``pool_size``, ``pool_available``,
    ``requests_waiting``, ``requests_num``, …). Pools that were never built
    are simply absent.
    """
    pools: dict[str, ConnectionPool | AsyncConnectionPool | None] = {
        "primary": _POOL,
        "primary_async": _ASYNC_POOL,
        "replica": _REPLICA_POOL,
        "replica_async": _ASYNC_REPLICA_POOL,
    }
    stats: dict[str, dict[str, int]] = {}
    for role, pool in pools.items():
        if pool is None:
            continue
        try:
            stats[role] = dict(pool.get_stats())
        except Exception:  # stats are best-effort telemetry
            logger.debug("pool_stats_unavailable", extra={"role": role}, exc_info=True)
            continue
    return stats
