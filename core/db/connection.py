"""
Database Connection Management.

Provides synchronous and asynchronous connection pools for PostgreSQL.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Iterator, Optional

from psycopg import AsyncConnection, Connection, Cursor
from psycopg.rows import RowFactory
from psycopg_pool import AsyncConnectionPool, ConnectionPool

from core.config import get_app_config, get_storage_config

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
            kwargs={"autocommit": True},
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
            kwargs={"autocommit": True},
            open=False,
        )
    return _ASYNC_POOL


@contextmanager
def get_connection() -> Iterator[Connection[object]]:
    """
    Returns a PostgreSQL database connection from the shared connection pool.
    """

    pool = _get_pool()
    # Ensure pool is open (idempotent check)
    try:
        pool.check()
    except Exception:
        # If check fails (e.g. pool not open), try to open
        pool.open()

    with pool.connection(timeout=DB_POOL_TIMEOUT) as connection:
        if getattr(connection, "_app_timezone", None) != APP_TIMEZONE_NAME:
            with connection.cursor() as _cursor:
                _cursor.execute(f"SET TIME ZONE '{APP_TIMEZONE_NAME}'")
            setattr(connection, "_app_timezone", APP_TIMEZONE_NAME)

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
    """
    pool = _get_async_pool()
    # Ensure pool is open (idempotent)
    await pool.open()

    async with pool.connection(timeout=DB_POOL_TIMEOUT) as connection:
        # Timezone handling skipped for async as noted in original code
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
    global _POOL
    if _POOL is not None:
        _POOL.close()


async def close_async_pool() -> None:
    """Explicitly closes the asynchronous connection pool."""
    global _ASYNC_POOL
    if _ASYNC_POOL is not None:
        await _ASYNC_POOL.close()
