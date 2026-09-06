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

from core.config import get_app_config, get_storage_config
from core.db._tracking import TrackingAsyncCursor, TrackingCursor, _track_db_query
from core.observability.logging import get_logger

__all__ = ["TrackingAsyncCursor", "TrackingCursor", "_track_db_query"]

_storage_config = get_storage_config()
_app_config = get_app_config()

POSTGRES_ENABLED = _storage_config.postgres_enabled
DB_CONNINFO = _storage_config.conninfo
DB_REPLICA_CONNINFO = _storage_config.replica_conninfo
DB_POOL_MIN_SIZE = _storage_config.db_pool_min_size
DB_POOL_MAX_SIZE = _storage_config.db_pool_max_size
DB_POOL_TIMEOUT = _storage_config.db_pool_timeout
APP_TIMEZONE_NAME = _app_config.app_timezone
# Opt-in Row-Level-Security: bind the request tenant to the DB session on every
# checkout so RLS policies can isolate rows. OFF by default → the apply hook is
# skipped entirely and the connection path is byte-identical to before.
DB_RLS_ENABLED = _storage_config.db_rls_enabled

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


def _sync_apply_timezone(connection: Connection[object]) -> None:
    """Apply the configured timezone to a sync connection once per checkout."""
    if getattr(connection, "_app_timezone", None) == APP_TIMEZONE_NAME:
        return

    with connection.cursor() as cursor:
        # PostgreSQL doesn't accept bind placeholders in `SET TIME ZONE`,
        # but `set_config()` does and avoids string interpolation here.
        cursor.execute("SELECT set_config('TimeZone', %s, false)", (APP_TIMEZONE_NAME,))

    # Dynamic marker attribute — psycopg's Connection doesn't declare it.
    setattr(connection, "_app_timezone", APP_TIMEZONE_NAME)  # noqa: B010


async def _async_apply_timezone(connection: AsyncConnection[object]) -> None:
    """Apply the configured timezone to an async connection once per checkout."""
    if getattr(connection, "_app_timezone", None) == APP_TIMEZONE_NAME:
        return

    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT set_config('TimeZone', %s, false)", (APP_TIMEZONE_NAME,)
        )

    # Dynamic marker attribute — psycopg's AsyncConnection doesn't declare it.
    setattr(connection, "_app_timezone", APP_TIMEZONE_NAME)  # noqa: B010


def _current_tenant_for_session() -> str:
    """Resolve the tenant to bind to the DB session, defensively.

    Outside a request (background task, script) the tenant contextvar may be
    unset; under ``strict_tenant_isolation`` that raises. RLS session binding
    must never break such callers, so we degrade to ``"default"`` rather than
    propagate. Request traffic always has a tenant bound upstream.
    """
    from core.context import TenantContextError, get_current_tenant_id

    try:
        return get_current_tenant_id()
    except TenantContextError:
        return "default"


def _sync_apply_tenant(connection: Connection[object]) -> None:
    """Bind ``app.tenant_id`` to a sync connection for RLS (opt-in).

    A pooled connection serves different tenants across requests, so the GUC
    must always reflect the current one — but re-issuing ``set_config`` when
    the bound tenant is *unchanged* costs one full round-trip per checkout
    for nothing. The last-applied tenant is memoized on the connection
    (``set_config(..., false)`` is session-scoped, so it survives checkouts on
    the same physical connection) and only a tenant change re-applies it.
    """
    tenant = _current_tenant_for_session()
    if getattr(connection, "_app_tenant_id", None) == tenant:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.tenant_id', %s, false)",
            (tenant,),
        )
    # Dynamic marker attribute — psycopg's Connection doesn't declare it.
    setattr(connection, "_app_tenant_id", tenant)  # noqa: B010


async def _async_apply_tenant(connection: AsyncConnection[object]) -> None:
    """Async counterpart of :func:`_sync_apply_tenant`."""
    tenant = _current_tenant_for_session()
    if getattr(connection, "_app_tenant_id", None) == tenant:
        return
    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT set_config('app.tenant_id', %s, false)",
            (tenant,),
        )
    # Dynamic marker attribute — psycopg's AsyncConnection doesn't declare it.
    setattr(connection, "_app_tenant_id", tenant)  # noqa: B010


def _get_pool() -> ConnectionPool:
    """Get or initialize the synchronous connection pool."""
    global _POOL
    if _POOL is None:
        if not POSTGRES_ENABLED:
            raise RuntimeError("PostgreSQL is disabled (POSTGRES_ENABLED=false).")
        _POOL = ConnectionPool(
            conninfo=DB_CONNINFO,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout=DB_POOL_TIMEOUT,
            kwargs={
                "autocommit": True,
                "options": _storage_config.session_options,
                "cursor_factory": TrackingCursor,
            },
            open=False,
        )
    return _POOL


def _get_async_pool() -> AsyncConnectionPool:
    """Get or initialize the asynchronous connection pool."""
    global _ASYNC_POOL
    if _ASYNC_POOL is None:
        if not POSTGRES_ENABLED:
            raise RuntimeError("PostgreSQL is disabled (POSTGRES_ENABLED=false).")
        _ASYNC_POOL = AsyncConnectionPool(
            conninfo=DB_CONNINFO,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout=DB_POOL_TIMEOUT,
            kwargs={
                "autocommit": True,
                "options": _storage_config.session_options,
                "cursor_factory": TrackingAsyncCursor,
            },
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
        if DB_RLS_ENABLED:
            _sync_apply_tenant(connection)
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
        if DB_RLS_ENABLED:
            await _async_apply_tenant(connection)
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
            kwargs={
                "autocommit": True,
                "options": _storage_config.session_options,
                "cursor_factory": TrackingCursor,
            },
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
            kwargs={
                "autocommit": True,
                "options": _storage_config.session_options,
                "cursor_factory": TrackingAsyncCursor,
            },
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
        if DB_RLS_ENABLED:
            _sync_apply_tenant(connection)
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
        if DB_RLS_ENABLED:
            await _async_apply_tenant(connection)
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

    Returns:
        True when the pool is warm (or already was), False when PostgreSQL
        is disabled or the warmup attempt failed.
    """
    global _ASYNC_POOL_OPENED
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
            continue
    return stats
