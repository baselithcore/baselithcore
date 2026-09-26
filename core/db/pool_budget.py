"""Startup check: does the pool budget fit the server's connection limit?

Every uvicorn worker is its own process with its own async pool, so the
connections a deployment can open at peak are ``DB_POOL_MAX_SIZE`` times the
worker count — not ``DB_POOL_MAX_SIZE``. With the defaults (20 per pool) four
workers already ask for 80 of PostgreSQL's default 100 ``max_connections``,
before the migrations job, a second replica, the RQ worker or a psql session
take theirs. The overflow never shows up at boot: it surfaces under load as
``FATAL: sorry, too many clients already`` on whichever process loses the
race, which reads as a database outage rather than a sizing mistake.

The server reports its own limit, so the arithmetic is done once at startup
and a mismatch is logged while it is still a configuration problem.
Informational only — it never blocks startup and never raises.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config.concurrency import get_web_concurrency
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["ConnectionBudget", "check_connection_budget"]

_LIMITS_SQL = (
    "SELECT current_setting('max_connections')::int, "
    "current_setting('superuser_reserved_connections')::int"
)


@dataclass(frozen=True, slots=True)
class ConnectionBudget:
    """Peak demand of this process's workers against the server limit.

    Attributes:
        pool_max_size: ``DB_POOL_MAX_SIZE`` (per pool, per worker).
        workers: Server processes sharing the database (``WEB_CONCURRENCY``).
        max_connections: The server's ``max_connections``.
        reserved: ``superuser_reserved_connections`` (unavailable to the app).
    """

    pool_max_size: int
    workers: int
    max_connections: int
    reserved: int

    @property
    def demand(self) -> int:
        """Connections the async pools of every worker may hold at peak."""
        return self.pool_max_size * self.workers

    @property
    def available(self) -> int:
        """Connections a non-superuser role can actually open."""
        return max(self.max_connections - self.reserved, 0)

    @property
    def exceeded(self) -> bool:
        """True when peak demand is more than the server will hand out."""
        return self.demand > self.available


async def check_connection_budget() -> ConnectionBudget | None:
    """Compare the pool budget with the server limit and warn on overflow.

    Returns:
        The computed budget, or ``None`` when the limits could not be read
        (PostgreSQL disabled or unreachable, or a proxy such as PgBouncer that
        does not expose the settings). Never raises.
    """
    from core.db import connection

    if not connection.POSTGRES_ENABLED:
        return None
    try:
        async with connection.get_async_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(_LIMITS_SQL)
                row = await cur.fetchone()
    except Exception as exc:  # informational check, must not fail startup
        logger.debug("db_connection_budget_unavailable", extra={"error": str(exc)})
        return None
    if not isinstance(row, tuple) or len(row) < 2:
        return None
    budget = ConnectionBudget(
        pool_max_size=connection.DB_POOL_MAX_SIZE,
        workers=get_web_concurrency(),
        max_connections=int(row[0]),
        reserved=int(row[1]),
    )
    if budget.exceeded:
        logger.warning(
            "db_pool_budget_exceeds_max_connections",
            extra={
                "pool_max_size": budget.pool_max_size,
                "workers": budget.workers,
                "demand": budget.demand,
                "available": budget.available,
                "hint": (
                    "Lower DB_POOL_MAX_SIZE to at most "
                    f"{budget.available // budget.workers} per worker, raise "
                    "max_connections, or put PgBouncer in front of PostgreSQL."
                ),
            },
        )
    return budget
