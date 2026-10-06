"""The lifespan teardown runs every step, whatever an earlier one did."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.api import _shutdown
from core.api._shutdown import ShutdownStep, run_shutdown_steps


async def test_a_failing_early_step_does_not_skip_later_steps() -> None:
    ran: list[str] = []

    async def boom() -> None:
        ran.append("boom")
        raise RuntimeError("bridge exploded")

    async def drain_pools() -> None:
        ran.append("pools")

    def flush_otel() -> None:
        ran.append("otel")

    failed = await run_shutdown_steps(
        [
            ShutdownStep("runtime_services", boom),
            ShutdownStep("db_pool", drain_pools),
            ShutdownStep("telemetry", flush_otel),
        ]
    )

    assert ran == ["boom", "pools", "otel"]
    assert failed == ["runtime_services"]


async def test_a_failure_is_logged_with_the_step_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom() -> None:
        raise RuntimeError("bridge exploded")

    with patch.object(_shutdown, "logger") as fake_logger:
        await run_shutdown_steps([ShutdownStep("runtime_services", boom)])

    fake_logger.error.assert_called_once()
    args, kwargs = fake_logger.error.call_args
    assert "runtime_services" in args
    assert kwargs.get("exc_info") is True


async def test_a_hung_step_is_bounded_by_its_timeout_and_the_rest_still_run() -> None:
    ran: list[str] = []

    async def hang() -> None:
        await asyncio.sleep(60)

    async def after() -> None:
        ran.append("after")

    failed = await run_shutdown_steps(
        [ShutdownStep("hung", hang, timeout=0.05), ShutdownStep("after", after)]
    )

    assert failed == ["hung"]
    assert ran == ["after"]


async def test_shutdown_application_still_closes_pools_when_early_steps_raise() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    close_pool = AsyncMock()
    shutdown_telemetry = MagicMock()
    with (
        patch(
            "core.api._runtime_services.stop_runtime_services",
            AsyncMock(side_effect=RuntimeError("x")),
        ),
        patch(
            "core.api._runtime_services.drain_orchestrator",
            AsyncMock(side_effect=RuntimeError("y")),
        ),
        patch(
            "core.di.lazy_registry.LazyServiceRegistry.shutdown_all",
            AsyncMock(side_effect=ValueError("z")),
        ),
        patch("core.services.bootstrap.bootstrapper.shutdown", AsyncMock()),
        patch("core.observability.otel.shutdown_telemetry", shutdown_telemetry),
        patch("core.db.connection.close_async_pool", close_pool),
    ):
        failed = await _shutdown.shutdown_application(app, set())

    close_pool.assert_awaited_once()
    shutdown_telemetry.assert_called_once()
    assert {"runtime_services", "orchestrator_drain", "lazy_registry"} <= set(failed)


def test_step_names_are_unique() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    names = [step.name for step in _shutdown.build_shutdown_steps(app, set())]
    assert len(names) == len(set(names))
    # Pools drain after everything that may still write through them.
    assert names.index("db_pool") > names.index("orchestrator_drain")
    assert names.index("db_pool") > names.index("usage_sinks")


async def test_deadline_clips_slow_steps_so_critical_flushes_still_run() -> None:
    ran: list[str] = []

    async def hang() -> None:
        await asyncio.sleep(60)

    async def flush() -> None:
        await asyncio.sleep(0.05)
        ran.append("flush")

    failed = await run_shutdown_steps(
        [
            ShutdownStep("slow_drain", hang, timeout=30.0),
            ShutdownStep("slow_plugins", hang, timeout=30.0),
            ShutdownStep("flush", flush, timeout=5.0, critical=True, reserve=0.2),
        ],
        deadline=0.4,
    )

    assert ran == ["flush"]
    assert failed == ["slow_drain", "slow_plugins"]


async def test_a_non_critical_step_with_no_budget_left_is_skipped() -> None:
    ran: list[str] = []

    async def hang() -> None:
        await asyncio.sleep(60)

    async def later() -> None:
        ran.append("later")

    async def critical() -> None:
        ran.append("critical")

    failed = await run_shutdown_steps(
        [
            ShutdownStep("hung", hang, timeout=None),
            ShutdownStep("later", later),
            ShutdownStep("critical", critical, critical=True, reserve=0.1),
        ],
        deadline=0.2,
    )

    assert failed == ["hung", "later"]
    assert ran == ["critical"]


async def test_a_critical_step_keeps_its_reserve_after_an_overrun() -> None:
    import time

    async def flush() -> None:
        await asyncio.sleep(0.05)

    def blocking_overrun() -> None:
        time.sleep(0.25)  # sync: cannot be interrupted, eats the deadline

    failed = await run_shutdown_steps(
        [
            ShutdownStep("sync_overrun", blocking_overrun),
            ShutdownStep("flush", flush, critical=True, reserve=0.15),
        ],
        deadline=0.2,
    )

    assert failed == []


def test_the_reserves_fit_inside_the_teardown_budget() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    steps = _shutdown.build_shutdown_steps(app, set())
    reserved = sum(s.reserve for s in steps if s.critical)
    assert reserved < _shutdown.TEARDOWN_BUDGET_S
    critical = {s.name for s in steps if s.critical}
    assert {
        "usage_sinks",
        "audit_events",
        "telemetry",
        "db_pool",
        "redis_pools",
    } <= critical


def test_pending_audit_events_flush_before_the_pools_close() -> None:
    app = SimpleNamespace(state=SimpleNamespace())
    names = [step.name for step in _shutdown.build_shutdown_steps(app, set())]
    assert names.index("audit_events") > names.index("plugins")
    assert names.index("audit_events") < names.index("db_pool")
    assert names.index("audit_events") < names.index("redis_pools")


@pytest.fixture(autouse=True)
def _quiet_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.CRITICAL)
