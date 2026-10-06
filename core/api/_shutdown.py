"""Ordered, isolated teardown for the app lifespan.

Extracted from :mod:`core.api.lifespan`. The teardown used to be one straight
sequence of awaits, so the first step that raised skipped every step after it:
a failing run-events bridge stop left the Postgres pool undrained and the
OpenTelemetry batch unflushed. Each step is now a named
:class:`ShutdownStep` run by :func:`run_shutdown_steps`, which bounds it by a
timeout, logs a failure with the step's name and the traceback, and always
moves on to the next one.

Order matters and is deliberate: everything that may still write (runtime
services, the orchestrator's memory writes, plugins, usage sinks, pending audit
events) stops before the pools those writes go through are closed.

The whole teardown also runs inside one deadline, :data:`TEARDOWN_BUDGET_S`.
Per-step timeouts alone did not fit the pod's termination grace: the steps
could add up to minutes, so a slow orchestrator drain or plugin shutdown ate
the time and SIGKILL landed before the usage ledger, the audit queue, the
telemetry batch and the pools were flushed. Steps marked ``critical`` declare
a ``reserve``; every step before them is clipped so the reserves of the
critical steps still ahead stay available, and a non-critical step that would
start with no budget left is skipped (reported as failed). The deployment
arithmetic this budget belongs to lives in the Helm chart's ``values.yaml``:

    terminationGracePeriodSeconds >= preStop sleep
                                     + GRACEFUL_SHUTDOWN_TIMEOUT (HTTP drain)
                                     + TEARDOWN_BUDGET_S
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Upper bound on one step unless the step declares its own. Shutdown runs
#: inside the orchestrator's grace period: one hung close must not consume it.
DEFAULT_STEP_TIMEOUT_S = 15.0

#: Wall-clock budget for the whole lifespan teardown. It starts *after*
#: uvicorn's HTTP drain, so the pod grace must cover preStop + drain + this.
TEARDOWN_BUDGET_S = 40.0


@dataclass(frozen=True)
class ShutdownStep:
    """One named teardown action.

    Attributes:
        name: Stable identifier used in the failure log line.
        action: Zero-argument callable; sync or returning an awaitable.
        timeout: Seconds the awaitable may take, or ``None`` for unbounded
            (still clipped by the teardown deadline when one is set).
            A synchronous action cannot be interrupted and is not bounded.
        critical: A flush or close that must run even when earlier steps
            used up their time: never skipped, and its ``reserve`` is held
            back from every step before it.
        reserve: Seconds guaranteed to a critical step out of the deadline.
            Ignored for non-critical steps.
    """

    name: str
    action: Callable[[], Awaitable[Any] | Any]
    timeout: float | None = DEFAULT_STEP_TIMEOUT_S
    critical: bool = False
    reserve: float = 0.0


def _step_timeout(
    step: ShutdownStep, remaining: float | None, reserved_after: float
) -> float | None:
    """The timeout one step actually gets under the deadline.

    Returns:
        ``None`` for unbounded, ``0.0`` or less when a non-critical step has
        no budget left and must be skipped.
    """
    if remaining is None:
        return step.timeout
    if step.critical:
        # Its own reserve is never taken by anyone else, so it always has at
        # least that, even when an earlier synchronous step overran.
        budget = max(remaining - reserved_after, step.reserve)
    else:
        budget = remaining - reserved_after
    if step.timeout is not None:
        budget = min(budget, step.timeout)
    return budget


async def run_shutdown_steps(
    steps: Sequence[ShutdownStep], *, deadline: float | None = None
) -> list[str]:
    """Run every step in order; a failure or timeout never stops the rest.

    Args:
        steps: The teardown, in execution order.
        deadline: Total seconds for the whole sequence, or ``None`` to bound
            each step only by its own timeout. With a deadline each step's
            timeout shrinks to what is left minus the reserves of the
            critical steps after it.

    Returns:
        The names of the steps that raised, timed out or were skipped for
        lack of budget, in order.
    """
    failed: list[str] = []
    started = time.monotonic()
    # reserves[i] = sum of the reserves of the critical steps after step i.
    reserves = [0.0] * len(steps)
    acc = 0.0
    for i in range(len(steps) - 1, -1, -1):
        reserves[i] = acc
        if steps[i].critical:
            acc += steps[i].reserve
    for index, step in enumerate(steps):
        remaining = (
            None if deadline is None else deadline - (time.monotonic() - started)
        )
        timeout = _step_timeout(step, remaining, reserves[index])
        if timeout is not None and timeout <= 0 and not step.critical:
            failed.append(step.name)
            logger.error("shutdown_step_skipped step=%s reason=budget", step.name)
            continue
        try:
            result = step.action()
            if inspect.isawaitable(result):
                if timeout is None:
                    await result
                else:
                    async with asyncio.timeout(max(timeout, 0.0)):
                        await result
        except Exception:
            failed.append(step.name)
            logger.error("shutdown_step_failed step=%s", step.name, exc_info=True)
    return failed


async def _cancel_background_tasks(tasks: set[asyncio.Task[Any]]) -> None:
    """Cancel fire-and-forget startup tasks (bootstrap, recovery sweep).

    A hung sweep would otherwise live until SIGKILL.
    """
    for task in list(tasks):
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()


async def _shutdown_plugins(app: Any) -> None:
    registry = getattr(app.state, "plugin_registry", None)
    if registry is None:
        return
    logger.info("🔌 Shutdown plugin system...")
    for plugin in registry.get_all():
        try:
            await plugin.shutdown()
        except Exception as exc:
            logger.error("Error shutting down plugin %s: %s", plugin.metadata.name, exc)


async def _shutdown_lazy_registry() -> None:
    from core.di.lazy_registry import get_lazy_registry

    await get_lazy_registry().shutdown_all()


async def _drain_usage_sinks() -> None:
    from core.services.llm.usage_sinks import drain_usage_sinks

    await drain_usage_sinks(timeout=5.0)  # in-flight ledger writes first


async def _flush_audit_events() -> None:
    from core.observability.audit import flush_pending_audit_events

    await flush_pending_audit_events(timeout=3.0)


async def _shutdown_sync_inference() -> None:
    from core.services.inference.sync_bridge import ashutdown_sync_inference

    await ashutdown_sync_inference()


async def _close_rate_limiter() -> None:
    from core.middleware.security import get_security_manager

    await get_security_manager().rate_limiter.close()


async def _shutdown_telemetry() -> None:
    # The OTel flush is synchronous and its exporter may wait on a dead
    # collector; off-loop it is bounded by the step timeout like the others,
    # so a hung export cannot starve the pool closes after it.
    from core.observability.otel import shutdown_telemetry

    await asyncio.to_thread(shutdown_telemetry)


async def _shutdown_bootstrapper() -> None:
    from core.services.bootstrap import bootstrapper

    await bootstrapper.shutdown()


async def _close_db_pool() -> None:
    # Drain shared pools explicitly instead of relying on GC: uvicorn has
    # already drained requests, and a rolling deploy releases server-side
    # connections promptly.
    from core.db.connection import close_async_pool

    await close_async_pool()


async def _close_redis_pools() -> None:
    from core.cache.redis_cache import close_redis_pools

    await close_redis_pools()


def _close_sync_redis_pools() -> None:
    # Graph, A2A nonce ledger, AP2 replay guard, sync limiter, scratchpad: a
    # separate registry from the async pools.
    from core.cache.redis_sync import close_sync_redis_pools

    close_sync_redis_pools()


def build_shutdown_steps(
    app: Any, background_tasks: set[asyncio.Task[Any]]
) -> list[ShutdownStep]:
    """The lifespan teardown, in order.

    Args:
        app: The FastAPI application whose ``state`` holds the services.
        background_tasks: The lifespan's fire-and-forget startup tasks.
    """
    from core.api import _runtime_services as runtime
    from core.api import startup_checks

    return [
        ShutdownStep("runtime_services", lambda: runtime.stop_runtime_services(app)),
        ShutdownStep("orchestrator_drain", runtime.drain_orchestrator, timeout=30.0),
        ShutdownStep(
            "background_tasks", lambda: _cancel_background_tasks(background_tasks)
        ),
        ShutdownStep(
            "retention_scheduler",
            lambda: startup_checks.stop_retention_scheduler(app),
        ),
        ShutdownStep(
            "regulatory_subsystems",
            lambda: startup_checks.stop_regulatory_subsystems(app),
        ),
        ShutdownStep("plugins", lambda: _shutdown_plugins(app), timeout=30.0),
        ShutdownStep("lazy_registry", _shutdown_lazy_registry),
        ShutdownStep("shared_clients", runtime.close_shared_clients),
        ShutdownStep(
            "usage_sinks", _drain_usage_sinks, timeout=6.0, critical=True, reserve=6.0
        ),
        ShutdownStep(
            "audit_events", _flush_audit_events, timeout=4.0, critical=True, reserve=4.0
        ),
        ShutdownStep("sync_inference", _shutdown_sync_inference),
        ShutdownStep("rate_limiter", _close_rate_limiter),
        ShutdownStep(
            "telemetry", _shutdown_telemetry, timeout=5.0, critical=True, reserve=5.0
        ),
        ShutdownStep("bootstrapper", _shutdown_bootstrapper),
        ShutdownStep(
            "db_pool", _close_db_pool, timeout=5.0, critical=True, reserve=3.0
        ),
        ShutdownStep(
            "redis_pools", _close_redis_pools, timeout=5.0, critical=True, reserve=2.0
        ),
        ShutdownStep("sync_redis_pools", _close_sync_redis_pools, critical=True),
    ]


async def shutdown_application(
    app: Any, background_tasks: set[asyncio.Task[Any]]
) -> list[str]:
    """Run the whole teardown; return the names of the steps that failed."""
    logger.info("🔻 Lifecycle shutdown: closing connections and bootstrapper.")
    failed = await run_shutdown_steps(
        build_shutdown_steps(app, background_tasks), deadline=TEARDOWN_BUDGET_S
    )
    if failed:
        logger.warning("⚠️ FastAPI backend stopped; failed steps: %s", failed)
    else:
        logger.info("✅ FastAPI backend stopped successfully.")
    return failed


__all__ = [
    "DEFAULT_STEP_TIMEOUT_S",
    "TEARDOWN_BUDGET_S",
    "ShutdownStep",
    "build_shutdown_steps",
    "run_shutdown_steps",
    "shutdown_application",
]
