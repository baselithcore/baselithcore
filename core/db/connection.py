"""
Database Connection Management.

Provides synchronous and asynchronous connection pools for PostgreSQL.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator, Optional

from psycopg import AsyncConnection, AsyncCursor, Connection, Cursor
from psycopg.rows import RowFactory
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from core.config import get_app_config, get_storage_config
from core.middleware.cost_control import (
    BudgetExceededError,
    _cost_context,
    cost_controller,
)


def _track_db_query(query: Any) -> None:
    """Increment request-scoped DB query counter; propagate only budget errors.

    Short-circuits when no cost-tracking context is active so we avoid the
    ``str(query)`` cost on the hot path of every query outside an HTTP request.
    """
    if _cost_context.get() is None:
        return

    try:
        text = query if isinstance(query, str) else str(query)
        cost_controller.track_query(text)
    except BudgetExceededError:
        raise
    except Exception:
        # Tracking must never break real queries.
        pass


class TrackingCursor(Cursor):
    """Sync psycopg cursor that reports executed queries to the cost controller."""

    def execute(self, query, params=None, *, prepare=None, binary=None):  # type: ignore[override]
        _track_db_query(query)
        return super().execute(query, params, prepare=prepare, binary=binary)

    def executemany(self, query, params_seq, *, returning=False):  # type: ignore[override]
        _track_db_query(query)
        return super().executemany(query, params_seq, returning=returning)


class TrackingAsyncCursor(AsyncCursor):
    """Async psycopg cursor that reports executed queries to the cost controller."""

    async def execute(self, query, params=None, *, prepare=None, binary=None):  # type: ignore[override]
        _track_db_query(query)
        return await super().execute(query, params, prepare=prepare, binary=binary)

    async def executemany(self, query, params_seq, *, returning=False):  # type: ignore[override]
        _track_db_query(query)
        return await super().executemany(query, params_seq, returning=returning)


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

_POOL: Optional[ConnectionPool] = None
_ASYNC_POOL: Optional[AsyncConnectionPool] = None
_POOL_OPENED: bool = False
_ASYNC_POOL_OPENED: bool = False

# Read-replica pools — created lazily only when DB_REPLICA_URL is configured.
_REPLICA_POOL: Optional[ConnectionPool] = None
_ASYNC_REPLICA_POOL: Optional[AsyncConnectionPool] = None
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

    setattr(connection, "_app_timezone", APP_TIMEZONE_NAME)


async def _async_apply_timezone(connection: AsyncConnection[object]) -> None:
    """Apply the configured timezone to an async connection once per checkout."""
    if getattr(connection, "_app_timezone", None) == APP_TIMEZONE_NAME:
        return

    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT set_config('TimeZone', %s, false)", (APP_TIMEZONE_NAME,)
        )

    setattr(connection, "_app_timezone", APP_TIMEZONE_NAME)


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

    Set on EVERY checkout (never cached): a pooled connection serves different
    tenants across requests, so the GUC must reflect the current one. A no-op
    unless ``DB_RLS_ENABLED``.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT set_config('app.tenant_id', %s, false)",
            (_current_tenant_for_session(),),
        )


async def _async_apply_tenant(connection: AsyncConnection[object]) -> None:
    """Async counterpart of :func:`_sync_apply_tenant`."""
    async with connection.cursor() as cursor:
        await cursor.execute(
            "SELECT set_config('app.tenant_id', %s, false)",
            (_current_tenant_for_session(),),
        )


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
                "options": "-c statement_timeout=30000",
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
                "options": "-c statement_timeout=30000",
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
    row_factory: Optional[RowFactory] = None,
) -> Iterator[Cursor[object]]:
    """
    Returns a ready-to-use cursor, optionally configured with a row factory.
    """

    with get_connection() as connection:
        with connection.cursor(row_factory=row_factory) as cursor:  # type: ignore[arg-type]
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
    row_factory: Optional[RowFactory] = None,
) -> AsyncIterator[Any]:
    """
    Returns an asynchronous ready-to-use cursor.
    Note: the 'Any' return annotation is used because AsyncCursor is generic.
    """
    async with get_async_connection() as connection:
        async with connection.cursor(row_factory=row_factory) as cursor:  # type: ignore[call-overload]
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
                "options": "-c statement_timeout=30000",
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
                "options": "-c statement_timeout=30000",
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
