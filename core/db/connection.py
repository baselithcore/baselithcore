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


def _track_db_query(query: Any) -> None:
    """Increment request-scoped DB query counter; propagate only budget errors."""
    from core.middleware.cost_control import BudgetExceededError, cost_controller

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
DB_POOL_MIN_SIZE = _storage_config.db_pool_min_size
DB_POOL_MAX_SIZE = _storage_config.db_pool_max_size
DB_POOL_TIMEOUT = _storage_config.db_pool_timeout
APP_TIMEZONE_NAME = _app_config.app_timezone

_POOL: Optional[ConnectionPool] = None
_ASYNC_POOL: Optional[AsyncConnectionPool] = None
_POOL_OPENED: bool = False
_ASYNC_POOL_OPENED: bool = False


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


def close_pool() -> None:
    """Explicitly closes the connection pool (useful during worker shutdown)."""
    global _POOL, _POOL_OPENED
    if _POOL is not None:
        _POOL.close()
        _POOL_OPENED = False


async def close_async_pool() -> None:
    """Explicitly closes the asynchronous connection pool."""
    global _ASYNC_POOL, _ASYNC_POOL_OPENED
    if _ASYNC_POOL is not None:
        await _ASYNC_POOL.close()
        _ASYNC_POOL_OPENED = False
