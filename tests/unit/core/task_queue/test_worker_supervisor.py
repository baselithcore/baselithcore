"""The multi-process worker supervisor forwards stops and restarts crashes.

Kubernetes signals PID 1 only. The extra RQ workers used to be plain child
processes the parent joined forever: they never saw SIGTERM, so the parent
hung until SIGKILL, and a child that died was never replaced.
"""

from __future__ import annotations

import os
import signal
from typing import Any
from unittest.mock import patch

import pytest

from core.task_queue import worker
from core.task_queue.worker import WorkerSupervisor

pytest.importorskip("rq")


class _FakeChild:
    _next_pid = 10_000

    def __init__(self, *, exits_on_signal: bool = True) -> None:
        _FakeChild._next_pid += 1
        self.pid: int | None = None
        self.exitcode: int | None = None
        self.started = False
        self.alive = False
        self.exits_on_signal = exits_on_signal
        self.terminated = False
        self.killed = False
        self.joins: list[float | None] = []
        self._pid = _FakeChild._next_pid

    def start(self) -> None:
        self.started = True
        self.alive = True
        self.pid = self._pid

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.joins.append(timeout)

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.alive = False
        self.exitcode = -9

    def die(self, code: int = 1) -> None:
        self.alive = False
        self.exitcode = code


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _supervisor(count: int, clock: _Clock, **kw) -> tuple[WorkerSupervisor, list]:
    spawned: list[_FakeChild] = []

    def spawn() -> _FakeChild:
        child = _FakeChild(**kw)
        spawned.append(child)
        return child

    sup = WorkerSupervisor(
        spawn, count, stop_timeout=10.0, clock=clock, sleep=lambda _s: None
    )
    return sup, spawned


def test_starts_count_children() -> None:
    sup, spawned = _supervisor(3, _Clock())
    sup.supervise_once()
    assert len(spawned) == 3
    assert all(c.started for c in spawned)


def test_stop_forwards_the_signal_to_every_child() -> None:
    sup, spawned = _supervisor(2, _Clock())
    sup.supervise_once()
    with patch.object(os, "kill") as fake_kill:
        sup.request_stop(signal.SIGTERM)
    assert sup.stopping
    assert sorted(c.args for c in fake_kill.call_args_list) == sorted(
        (c.pid, signal.SIGTERM) for c in spawned
    )


def test_a_dead_child_is_restarted_with_backoff() -> None:
    clock = _Clock()
    sup, spawned = _supervisor(1, clock)
    sup.supervise_once()
    spawned[0].die()

    clock.now = 1.0  # crashed after 1s: not stable, back off
    sup.supervise_once()
    assert len(spawned) == 1  # first restart waits _RESTART_BACKOFF_INITIAL_S

    clock.now = 2.0
    sup.supervise_once()
    assert len(spawned) == 2 and spawned[1].started

    # Crash again quickly: the delay doubles.
    spawned[1].die()
    clock.now = 2.5
    sup.supervise_once()
    clock.now = 4.0
    sup.supervise_once()
    assert len(spawned) == 2
    clock.now = 4.6
    sup.supervise_once()
    assert len(spawned) == 3


def test_a_child_that_ran_long_restarts_at_once() -> None:
    clock = _Clock()
    sup, spawned = _supervisor(1, clock)
    sup.supervise_once()
    clock.now = worker._STABLE_RUN_S + 1
    spawned[0].die(0)
    sup.supervise_once()
    assert len(spawned) == 2


def test_no_restart_once_stopping() -> None:
    sup, spawned = _supervisor(1, _Clock())
    sup.supervise_once()
    with patch.object(os, "kill"):
        sup.request_stop()
    spawned[0].die()
    sup.supervise_once()
    assert len(spawned) == 1


def test_shutdown_terminates_then_kills_a_stuck_child() -> None:
    sup, spawned = _supervisor(1, _Clock())
    sup.supervise_once()
    stuck = spawned[0]
    sup.shutdown()
    assert stuck.joins[0] == 10.0  # bounded by stop_timeout
    assert stuck.terminated
    assert stuck.killed


def test_shutdown_leaves_a_clean_exit_alone() -> None:
    sup, spawned = _supervisor(1, _Clock())
    sup.supervise_once()
    spawned[0].die(0)
    sup.shutdown()
    assert not spawned[0].terminated and not spawned[0].killed


def test_run_installs_forwarding_and_restores_handlers() -> None:
    before = signal.getsignal(signal.SIGTERM)
    clock = _Clock()
    sup, spawned = _supervisor(2, clock)

    def sleep(_s: float) -> None:
        # Simulate the orchestrator's SIGTERM hitting PID 1 mid-loop.
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with patch.object(os, "kill") as fake_kill:
            handler(signal.SIGTERM, None)
        assert fake_kill.call_count == 2
        for child in spawned:
            child.die(0)

    sup._sleep = sleep
    sup.run()

    assert sup.stopping
    assert signal.getsignal(signal.SIGTERM) is before


def test_request_stop_in_a_forked_copy_does_nothing() -> None:
    sup, spawned = _supervisor(1, _Clock())
    sup.supervise_once()
    sup._pid = -1  # as if this were the copy inherited by a forked child
    with patch.object(os, "kill") as fake_kill:
        sup.request_stop()
    fake_kill.assert_not_called()
    assert not sup.stopping


def test_child_worker_leaves_the_terminal_process_group() -> None:
    """A terminal Ctrl-C must reach the child once (via the supervisor).

    In the supervisor's process group the child would also get the terminal's
    SIGINT, and RQ reads that second signal as a cold shutdown.
    """
    with (
        patch.object(os, "setpgrp", create=True) as fake_setpgrp,
        patch.object(signal, "signal"),
        patch.object(worker, "_connect"),
        patch.object(worker, "build_worker") as fake_build,
    ):
        worker.run_worker("redis://localhost:6379/0", ["default"])
    fake_setpgrp.assert_called_once_with()
    fake_build.return_value.work.assert_called_once_with(with_scheduler=True)


def test_stop_during_spawn_still_reaches_the_new_child() -> None:
    """A signal between start() and the slot assignment must not be lost."""
    sup, spawned = _supervisor(1, _Clock())
    original_spawn = sup._spawn

    def spawn_then_signal() -> Any:
        child = original_spawn()
        real_start = child.start

        def start() -> None:
            real_start()
            sup.request_stop()  # handler runs before slot.process is set

        child.start = start
        return child

    sup._spawn = spawn_then_signal
    with patch.object(os, "kill") as fake_kill:
        sup.supervise_once()
    assert fake_kill.call_count == 1
    assert fake_kill.call_args.args[1] == signal.SIGTERM


def test_leaving_run_on_an_exception_signals_children_first() -> None:
    sup, spawned = _supervisor(2, _Clock())

    def boom(_s: float) -> None:
        raise RuntimeError("supervisor bug")

    sup._sleep = boom
    sup._stop_timeout = 0.0
    with patch.object(os, "kill") as fake_kill, pytest.raises(RuntimeError):
        sup.run()
    assert fake_kill.call_count == 2
