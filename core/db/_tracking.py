"""Cost-tracking psycopg cursors.

Every executed statement is reported to the request-scoped cost controller, so
a runaway agent loop hits its SQL budget instead of the database. Split out of
``core.db.connection`` to keep that module under the file-size cap; the names
are re-exported there, which is where callers still import them from.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncCursor, Cursor

from core.middleware.cost_control import (
    BudgetExceededError,
    _cost_context,
    cost_controller,
)

__all__ = ["TrackingAsyncCursor", "TrackingCursor", "_track_db_query"]


def _track_db_query(query: Any) -> None:
    """Increment request-scoped DB query counter; propagate only budget errors.

    Short-circuits when no cost-tracking context is active. The raw query
    object is passed through untouched — stringifying psycopg ``Composed``/
    ``SQL`` objects on every statement is wasted work unless a positive
    ``sql_query_limit`` actually consumes the text (see ``track_sql_query``).
    """
    if _cost_context.get() is None:
        return

    try:
        # Relational SQL is tracked under the SQL budget, NOT the graph (Cypher)
        # budget: an agentic request runs hundreds of SQL statements and must
        # never be gated by the tight graph limit (which is for actual graph DB
        # traversals). Default SQL limit is unlimited — see track_sql_query.
        cost_controller.track_sql_query(query)
    except BudgetExceededError:
        raise
    except Exception:
        # Tracking must never break real queries.
        pass


class TrackingCursor(Cursor):
    """Sync psycopg cursor that reports executed queries to the cost controller."""

    def execute(  # type: ignore[override]
        self,
        query: Any,
        params: Any = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> Any:
        _track_db_query(query)
        return super().execute(query, params, prepare=prepare, binary=binary)

    def executemany(  # type: ignore[override]
        self, query: Any, params_seq: Any, *, returning: bool = False
    ) -> Any:
        _track_db_query(query)
        return super().executemany(query, params_seq, returning=returning)


class TrackingAsyncCursor(AsyncCursor):
    """Async psycopg cursor that reports executed queries to the cost controller."""

    async def execute(  # type: ignore[override]
        self,
        query: Any,
        params: Any = None,
        *,
        prepare: bool | None = None,
        binary: bool | None = None,
    ) -> Any:
        _track_db_query(query)
        return await super().execute(query, params, prepare=prepare, binary=binary)

    async def executemany(  # type: ignore[override]
        self, query: Any, params_seq: Any, *, returning: bool = False
    ) -> Any:
        _track_db_query(query)
        return await super().executemany(query, params_seq, returning=returning)
