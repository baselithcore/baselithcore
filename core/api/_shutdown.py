"""Ordered, isolated teardown for the app lifespan.

Extracted from :mod:`core.api.lifespan`. The teardown used to be one straight
sequence of awaits, so the first step that raised skipped every step after it:
a failing run-events bridge stop left the Postgres pool undrained and the
OpenTelemetry batch unflushed. Each step is now a named
:class:`ShutdownStep` run by :func:`run_shutdown_steps`, which bounds it by a
timeout, logs a failure with the step's name and the traceback, and always
moves on to the next one.

Order matters and is deliberate: everything that may still write (runtime
services, the orchestrator's memory writes, plugins, usage sinks) stops
before the pools those writes go through are closed.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Upper bound on one step unless the step declares its own. Shutdown runs
#: inside the orchestrator's grace period: one hung close must not consume it.
DEFAULT_STEP_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class ShutdownStep:
    """One named teardown action.

    Attributes:
        name: Stable identifier used in the failure log line.
        action: Zero-argument callable; sync or returning an awaitable.
        timeout: Seconds the awaitable may take, or ``None`` for unbounded.
            A synchronous action cannot be interrupted and is not bounded.
    """

    name: str
    action: Callable[[], Awaitable[Any] | Any]
    timeout: float | None = DEFAULT_STEP_TIMEOUT_S


async def run_shutdown_steps(steps: Sequence[ShutdownStep]) -> list[str]:
    """Run every step in order; a failure or timeout never stops the rest.

    Args:
        steps: The teardown, in execution order.

    Returns:
        The names of the steps that raised or timed out, in order.
    """
    failed: list[str] = []
    for step in steps:
        try:
            result = step.action()
            if inspect.isawaitable(result):
                if step.timeout is None:
                    await result
                else:
                    async with asyncio.timeout(step.timeout):
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


async def _shutdown_sync_inference() -> None:
    from core.services.inference.sync_bridge import ashutdown_sync_inference

    await ashutdown_sync_inference()


async def _close_rate_limiter() -> None:
    from core.middleware.security import get_security_manager

    await get_security_manager().rate_limiter.close()


def _shutdown_telemetry() -> Any:
    from core.observability.otel import shutdown_telemetry

    return shutdown_telemetry()


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
        ShutdownStep("usage_sinks", _drain_usage_sinks),
        ShutdownStep("sync_inference", _shutdown_sync_inference),
        ShutdownStep("rate_limiter", _close_rate_limiter),
        ShutdownStep("telemetry", _shutdown_telemetry),
        ShutdownStep("bootstrapper", _shutdown_bootstrapper),
        ShutdownStep("db_pool", _close_db_pool),
        ShutdownStep("redis_pools", _close_redis_pools),
        ShutdownStep("sync_redis_pools", _close_sync_redis_pools),
    ]


async def shutdown_application(
    app: Any, background_tasks: set[asyncio.Task[Any]]
) -> list[str]:
    """Run the whole teardown; return the names of the steps that failed."""
    logger.info("🔻 Lifecycle shutdown: closing connections and bootstrapper.")
    failed = await run_shutdown_steps(build_shutdown_steps(app, background_tasks))
    if failed:
        logger.warning("⚠️ FastAPI backend stopped; failed steps: %s", failed)
    else:
        logger.info("✅ FastAPI backend stopped successfully.")
    return failed


__all__ = [
    "DEFAULT_STEP_TIMEOUT_S",
    "ShutdownStep",
    "build_shutdown_steps",
    "run_shutdown_steps",
    "shutdown_application",
]
