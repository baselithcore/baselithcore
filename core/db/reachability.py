"""One cheap PostgreSQL reachability probe whose outcome boot steps share.

Every startup step that touches the database used to find out on its own that
it was down — each through a pool checkout that waits the full
``DB_POOL_TIMEOUT`` (30 s by default) before giving up. With PostgreSQL down,
the checkpoint-store DDL and the health probe waited in series and boot took
the better part of a minute before the app reported itself degraded.

:func:`probe_postgres` opens **one direct connection** (no pool, so no retry
loop) bounded by a short connect timeout, runs ``SELECT 1`` and records the
outcome. Steps that would otherwise block on the pool consult
:func:`postgres_known_unreachable` and skip — or defer to a background retry
— instead of waiting. A healthy database pays one extra connection at boot.

The outcome is only ever a *hint*: ``None`` (never probed — a CLI, a worker)
means "try as before", so nothing changes for entry points that never probe.
"""

from __future__ import annotations

import asyncio
import math

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Default bound on one probe: connect + ``SELECT 1``.
PROBE_TIMEOUT_S = 5.0

_last_outcome: bool | None = None


async def probe_postgres(timeout: float = PROBE_TIMEOUT_S) -> bool:
    """Check that PostgreSQL answers, and remember the answer.

    Args:
        timeout: Upper bound for connect plus ``SELECT 1``, in seconds.

    Returns:
        ``True`` when the database answered. ``False`` when it did not, or
        when PostgreSQL is disabled (nothing is recorded in that case).
    """
    global _last_outcome
    from core.config import get_storage_config

    config = get_storage_config()
    if not config.postgres_enabled:
        return False

    import psycopg

    try:
        async with asyncio.timeout(timeout):
            conn = await psycopg.AsyncConnection.connect(
                config.conninfo,
                autocommit=True,
                connect_timeout=max(1, math.ceil(timeout)),
            )
            try:
                await conn.execute("SELECT 1")
            finally:
                await conn.close()
    except Exception as exc:
        logger.warning("postgres_probe_failed error=%s", type(exc).__name__)
        _last_outcome = False
        return False
    _last_outcome = True
    return True


def last_postgres_probe() -> bool | None:
    """The outcome of the most recent probe, or ``None`` if none ran."""
    return _last_outcome


def postgres_known_unreachable() -> bool:
    """Whether the most recent probe saw PostgreSQL down."""
    return _last_outcome is False


def reset_postgres_probe() -> None:
    """Forget the recorded outcome (tests, config reloads)."""
    global _last_outcome
    _last_outcome = None


__all__ = [
    "PROBE_TIMEOUT_S",
    "last_postgres_probe",
    "postgres_known_unreachable",
    "probe_postgres",
    "reset_postgres_probe",
]
