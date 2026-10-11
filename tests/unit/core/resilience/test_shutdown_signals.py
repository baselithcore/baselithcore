"""Signal-driven paths of ``GracefulShutdown``: handler installation,
signal handling and the wait -> callbacks sequence.

No real signal handler is ever installed: the running loop's
``add_signal_handler`` is replaced per test.
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import MagicMock, call

import pytest

from baselith.core.resilience.bulkhead import Bulkhead
from baselith.core.resilience.shutdown import GracefulShutdown


def test_install_handlers_without_a_loop_only_warns() -> None:
    GracefulShutdown().install_handlers()  # must not raise


async def test_install_handlers_registers_sigterm_and_sigint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    add = MagicMock()
    monkeypatch.setattr(loop, "add_signal_handler", add)
    shutdown = GracefulShutdown()
    shutdown.install_handlers()
    assert add.call_args_list == [
        call(signal.SIGTERM, shutdown._handle_signal, signal.SIGTERM),
        call(signal.SIGINT, shutdown._handle_signal, signal.SIGINT),
    ]


def test_signal_before_waiting_marks_shutdown() -> None:
    shutdown = GracefulShutdown()
    shutdown._handle_signal(signal.SIGTERM)
    assert shutdown.is_shutting_down is True


async def test_signal_releases_waiter_and_runs_callbacks_once() -> None:
    shutdown = GracefulShutdown(timeout=5)
    ran: list[str] = []

    async def close_db() -> None:
        ran.append("db")

    def flush() -> None:
        ran.append("flush")

    shutdown.register(close_db)
    shutdown.register(flush)
    waiter = asyncio.create_task(shutdown.wait_for_shutdown())
    await asyncio.sleep(0)
    assert not waiter.done()

    shutdown._handle_signal(signal.SIGTERM)
    shutdown._handle_signal(signal.SIGINT)  # repeated signal is ignored
    await asyncio.wait_for(waiter, timeout=2)
    assert ran == ["flush", "db"]


async def test_slow_async_callback_times_out_and_the_rest_still_run() -> None:
    shutdown = GracefulShutdown(timeout=0.1)  # type: ignore[arg-type]
    ran: list[str] = []

    def first_registered() -> None:
        ran.append("first")

    async def hangs() -> None:
        await asyncio.sleep(30)

    shutdown.register(first_registered)
    shutdown.register(hangs)
    await asyncio.wait_for(shutdown._execute_callbacks(), timeout=2)
    assert ran == ["first"]


async def test_signal_received_before_waiting_is_not_lost() -> None:
    # Regression: wait_for_shutdown() used to create a fresh Event and wait
    # forever, while every later signal was dropped as "already shutting down".
    shutdown = GracefulShutdown()
    ran: list[str] = []
    shutdown.register(lambda: ran.append("cleanup"))
    shutdown._handle_signal(signal.SIGTERM)
    await asyncio.wait_for(shutdown.wait_for_shutdown(), timeout=0.2)
    assert ran == ["cleanup"]


async def test_bulkhead_reports_free_slots_while_busy() -> None:
    bulkhead = Bulkhead(max_concurrent=2, name="unit")
    gate = asyncio.Event()
    seen: list[int] = []

    @bulkhead
    async def work() -> None:
        seen.append(bulkhead.available)
        await gate.wait()

    task = asyncio.create_task(work())
    await asyncio.sleep(0)
    assert bulkhead.available == 1
    gate.set()
    await task
    assert seen == [1]
    assert bulkhead.available == 2


def test_bulkhead_rejects_sync_functions() -> None:
    with pytest.raises(TypeError, match="only works with async functions"):
        Bulkhead(max_concurrent=1)(lambda: None)  # type: ignore[arg-type]
