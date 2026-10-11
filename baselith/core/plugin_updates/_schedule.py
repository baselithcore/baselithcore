"""When the periodic update check actually calls GitHub.

Every worker process runs its own loop, but they share one cache directory.
Without coordination a deployment with N workers repeats every check N times —
about fifty GitHub calls each — which is what made a short check interval look
unaffordable. So a tick first looks at the shared report: when a sibling saved
one within half an interval, this worker reuses it instead of calling GitHub.
The first run is jittered so workers that boot together do not all find the
cache stale at the same instant.

A check asked for over the API (``check_now``) is never deferred: an operator
who clicks "Check now" wants GitHub, not a sibling's report.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from .cache import UpdateCache

logger = logging.getLogger(__name__)

#: The first run waits between 1x and this multiple of the base delay.
FIRST_RUN_SPREAD = 1.5


def first_run_delay(base: float) -> float:
    """``base`` stretched by a random factor, so co-booted workers spread out."""
    return base * random.uniform(1.0, FIRST_RUN_SPREAD)  # nosec B311 - scheduling, not crypto


def fresh_enough(
    cache: UpdateCache, interval: float, now: datetime | None = None
) -> bool:
    """Whether the shared report is younger than half an interval.

    Half, not a full interval: two workers whose ticks drift apart must not
    keep deferring to each other until the report is almost two intervals old.
    A report from the future (clock skew) is treated as stale.
    """
    report = cache.load()
    if report is None:
        return False
    checked_at = report.checked_at
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=UTC)
    age = ((now or datetime.now(UTC)) - checked_at).total_seconds()
    return 0 <= age < interval / 2


async def run_periodic(
    check: Callable[[], Awaitable[object]],
    cache: UpdateCache,
    interval: float,
    *,
    first_delay: float,
    on_reuse: Callable[[], None],
) -> None:
    """Run ``check`` every ``interval`` seconds unless a sibling just did.

    ``on_reuse`` runs instead of ``check`` when the shared report is fresh, so
    the caller can refresh what it derives from it (metrics). Errors are
    logged and the loop carries on; only cancellation stops it.
    """
    await asyncio.sleep(first_run_delay(first_delay))
    while True:
        try:
            if fresh_enough(cache, interval):
                on_reuse()
            else:
                await check()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("plugin_update_loop_error: %s", type(exc).__name__)
        await asyncio.sleep(interval)


__all__ = ["FIRST_RUN_SPREAD", "first_run_delay", "fresh_enough", "run_periodic"]
