"""The drain signal lets long-lived streams end before the shutdown timeout."""

from __future__ import annotations

import asyncio
import signal
import threading
from collections.abc import Iterator
from typing import Any

import pytest

from core.lifecycle import drain


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    drain._reset_for_tests()
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)
    drain._reset_for_tests()


async def test_waiters_wake_when_draining_starts() -> None:
    waiter = asyncio.ensure_future(drain.wait_for_drain())
    await asyncio.sleep(0)
    assert not waiter.done()
    drain.mark_draining()
    await asyncio.wait_for(waiter, timeout=1)
    assert drain.is_draining()


async def test_a_late_waiter_returns_at_once() -> None:
    drain.mark_draining()
    await asyncio.wait_for(drain.wait_for_drain(), timeout=1)


async def test_marking_from_another_thread_wakes_the_loop() -> None:
    waiter = asyncio.ensure_future(drain.wait_for_drain())
    await asyncio.sleep(0)
    threading.Thread(target=drain.mark_draining).start()
    await asyncio.wait_for(waiter, timeout=1)


def test_hook_marks_draining_then_calls_the_server_handler() -> None:
    calls: list[int] = []

    def server_handler(signum: int, _frame: Any) -> None:
        calls.append(signum)

    signal.signal(signal.SIGTERM, server_handler)
    assert drain.install_drain_signal_hook()
    handler = signal.getsignal(signal.SIGTERM)
    assert callable(handler)
    handler(signal.SIGTERM, None)
    assert drain.is_draining()
    assert calls == [signal.SIGTERM]


def test_hook_installs_once() -> None:
    signal.signal(signal.SIGTERM, lambda *_: None)
    drain.install_drain_signal_hook()
    first = signal.getsignal(signal.SIGTERM)
    drain.install_drain_signal_hook()
    assert signal.getsignal(signal.SIGTERM) is first


def test_off_the_main_thread_it_is_a_no_op() -> None:
    result: list[bool] = []
    thread = threading.Thread(
        target=lambda: result.append(drain.install_drain_signal_hook())
    )
    thread.start()
    thread.join()
    assert result == [False]


def test_marking_while_the_module_lock_is_held_does_not_deadlock() -> None:
    """A signal can land while ``_event_for`` holds the lock on this thread."""
    done = threading.Event()

    def inner() -> None:
        with drain._lock:
            drain.mark_draining()  # re-entrant: a plain Lock would hang here
        done.set()

    worker = threading.Thread(target=inner, daemon=True)
    worker.start()
    assert done.wait(timeout=2), "mark_draining deadlocked under the module lock"
    assert drain.is_draining()
