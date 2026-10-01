"""The updater service loop: single instance, heartbeat, execution, crash policy."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from core.config.plugin_update_apply import UpdateApplyConfig
from core.plugin_updates.apply import _events
from core.plugin_updates.apply import updater as updater_mod
from core.plugin_updates.apply.models import ApplyRun, RunKind, RunState
from core.plugin_updates.apply.store import RunStore
from core.plugin_updates.apply.updater import (
    UpdaterRefused,
    acquire_single_instance,
    build_executor,
    serve,
)


@pytest.fixture(autouse=True)
def _no_plugin_code(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Other tests in the session import plugins; that check has its own test."""
    if request.node.name != "test_serve_refuses_after_plugin_code_was_imported":
        monkeypatch.setattr(updater_mod, "_plugin_code_loaded", lambda: False)


CFG = UpdateApplyConfig(enabled=True, heartbeat_seconds=1, poll_seconds=0.2)


def _create(store: RunStore, *, approval: bool = False, **kw: Any) -> ApplyRun:
    return store.create(
        kind=RunKind.UPDATE,
        plugin="demo",
        from_version="1",
        to_version="2",
        tarball_sha256="f" * 64,
        requested_by="a",
        approval_required=approval,
        approval_ttl_seconds=60,
        **kw,
    )


class _Ex:
    """An executor whose ``execute`` is scripted per test."""

    def __init__(self, store: RunStore, stop: asyncio.Event, script: Any) -> None:
        self.store, self.stop, self.script = store, stop, script
        self.calls: list[str] = []

    async def execute(self, r: ApplyRun) -> ApplyRun:
        self.calls.append("execute")
        return await self.script(self, r)

    async def resume_activation(self, r: ApplyRun) -> ApplyRun:
        self.calls.append("resume")
        return self.store.transition(r.id, RunState.ROLLED_BACK, failure="interrupted")

    async def redo_rollback(self, r: ApplyRun) -> ApplyRun:  # pragma: no cover
        raise AssertionError

    def undo_swap(self, r: ApplyRun) -> None:
        self.calls.append("undo")


@pytest.fixture()
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    class _Bus:
        async def emit(self, name: str, data: dict[str, Any], **_: Any) -> int:
            seen.append(name)
            return 1

    monkeypatch.setattr(_events, "get_event_bus", lambda: _Bus())
    return seen


async def _serve(store: RunStore, ex: Any, stop: asyncio.Event, root: Path) -> None:
    await asyncio.wait_for(
        serve(
            config=CFG,
            store=store,
            executor=ex,
            overlay_root=root,
            core_version="1.50.0",
            stop=stop,
        ),
        timeout=10,
    )


def test_single_instance_lock(tmp_path: Path) -> None:
    fd = acquire_single_instance(tmp_path / "updater.lock")
    try:
        assert os.get_inheritable(fd) is False  # children never inherit the lock
        with pytest.raises(RuntimeError, match="another plugin updater"):
            acquire_single_instance(tmp_path / "updater.lock")
    finally:
        os.close(fd)


async def test_serve_heartbeats_executes_and_stops(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    stop = asyncio.Event()

    async def ok(ex: _Ex, r: ApplyRun) -> ApplyRun:
        ex.stop.set()
        return ex.store.transition(r.id, RunState.SUCCEEDED)

    await _serve(store, _Ex(store, stop, ok), stop, tmp_path)
    assert store.get(run.id).state is RunState.SUCCEEDED
    hb = store.read_heartbeat()
    assert hb is not None and hb.core_version == "1.50.0" and hb.overlay_writable
    assert hb.enabled is True


async def test_store_calls_run_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path)
    stop = asyncio.Event()
    threads: list[threading.Thread] = []
    real = store.next_approved

    def spy() -> ApplyRun | None:
        threads.append(threading.current_thread())
        stop.set()
        return real()

    monkeypatch.setattr(store, "next_approved", spy)
    await _serve(store, _Ex(store, stop, None), stop, tmp_path)
    assert threads and threading.main_thread() not in threads


async def test_crashed_execute_is_reconciled_at_once(
    tmp_path: Path, events: list[str]
) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    stop = asyncio.Event()

    async def crash(ex: _Ex, r: ApplyRun) -> ApplyRun:
        ex.store.transition(r.id, RunState.PREPARING)
        ex.store.transition(r.id, RunState.MIGRATING, previous_target=None)
        raise KeyError("boom")

    async def after(ex: _Ex, r: ApplyRun) -> ApplyRun:  # a second run proves we go on
        ex.stop.set()
        return ex.store.transition(r.id, RunState.SUCCEEDED)

    ex = _Ex(store, stop, crash)
    task = asyncio.create_task(_serve(store, ex, stop, tmp_path))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if store.active("demo") is None:
            break
    done = store.get(run.id)
    assert done.state is RunState.FAILED and done.failure == "interrupted"
    assert ex.calls == ["execute", "undo"]
    ex.script = after
    second = _create(store)
    await task
    assert store.get(second.id).state is RunState.SUCCEEDED


async def test_crash_that_leaves_the_run_approved_exits(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    stop = asyncio.Event()

    async def crash(ex: _Ex, r: ApplyRun) -> ApplyRun:
        raise OSError("disk")

    with pytest.raises(OSError):
        await _serve(store, _Ex(store, stop, crash), stop, tmp_path)
    assert store.get(run.id).state is RunState.APPROVED  # reconciled at next start
    fd = acquire_single_instance(store.root / "updater.lock")  # lock released
    os.close(fd)


async def test_rollback_failed_emits_the_event(
    tmp_path: Path, events: list[str]
) -> None:
    store = RunStore(tmp_path)
    _create(store)
    stop = asyncio.Event()

    async def fails(ex: _Ex, r: ApplyRun) -> ApplyRun:
        ex.stop.set()
        return ex.store.transition(r.id, RunState.ROLLBACK_FAILED, failure="x")

    await _serve(store, _Ex(store, stop, fails), stop, tmp_path)
    assert events == ["plugin.update_rollback_failed"]


async def test_stale_approvals_expire_on_every_tick(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    stop = asyncio.Event()
    old = _create(store, approval=True, now=datetime.now(UTC) - timedelta(seconds=59))
    task = asyncio.create_task(_serve(store, _Ex(store, stop, None), stop, tmp_path))
    await asyncio.sleep(0.3)
    assert store.get(old.id).state is RunState.AWAITING_APPROVAL
    late = store.get(old.id)
    assert late is not None and late.approval_expires_at is not None
    for _ in range(100):
        await asyncio.sleep(0.1)
        if store.get(old.id).state is RunState.EXPIRED:
            break
    stop.set()
    await task
    assert store.get(old.id).state is RunState.EXPIRED


async def test_serve_refuses_after_plugin_code_was_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "plugins.fake", object())
    stop = asyncio.Event()
    store = RunStore(tmp_path)
    with pytest.raises(UpdaterRefused, match="plugin code"):
        await _serve(store, _Ex(store, stop, None), stop, tmp_path)


def test_build_executor_refuses_without_an_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BASELITH_PLUGIN_OVERLAY_DIR", raising=False)
    with pytest.raises(UpdaterRefused, match="BASELITH_PLUGIN_OVERLAY_DIR"):
        build_executor(CFG)


def test_build_executor_wires_the_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BASELITH_PLUGIN_OVERLAY_DIR", str(tmp_path))
    ex, root = build_executor(CFG.model_copy(update={"state_dir": tmp_path / "s"}))
    assert root == tmp_path and ex.overlay_root == tmp_path


async def test_serve_drives_the_real_executor(env: Any) -> None:
    stop = asyncio.Event()
    real = env.ex.execute

    async def once(r: ApplyRun) -> ApplyRun:
        done = await real(r)
        stop.set()
        return done

    env.ex.execute = once  # type: ignore[method-assign]
    await _serve(env.store, env.ex, stop, env.overlay)
    assert env.store.get(env.run.id).state is RunState.SUCCEEDED
    assert env.store.active("demo") is None


async def test_reconcile_resumes_a_real_activating_run(env: Any) -> None:
    from core.plugin_updates.apply.reconcile import reconcile

    await env.ex.execute(env.run)  # installed 1.2.0
    again = env.store.create(
        kind=RunKind.ROLLBACK,
        plugin="demo",
        from_version="1.2.0",
        to_version="1.1.0",
        tarball_sha256=None,
        requested_by="bob",
        approval_required=False,
        approval_ttl_seconds=60,
        target=None,
    )
    for state in (RunState.PREPARING, RunState.MIGRATING, RunState.ACTIVATING):
        env.store.transition(
            again.id, state, previous_target="demo-1.2.0", must_stay_active=[]
        )
    env.ex._swap_locked("demo", None, again.id)  # switched, then the updater died
    assert await reconcile(env.store, env.ex) == [again.id]
    done = env.store.get(again.id)
    assert done.state is RunState.SUCCEEDED, done.message


async def test_heartbeat_continues_while_a_run_finishes_after_stop(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    stop = asyncio.Event()
    beats: list[datetime] = []

    async def slow(ex: _Ex, r: ApplyRun) -> ApplyRun:
        ex.stop.set()  # SIGTERM arrives mid-run
        at = datetime.now(UTC)
        await asyncio.sleep(2.5)
        hb = ex.store.read_heartbeat()
        assert hb is not None
        beats.append(hb.at)
        beats.append(at)
        return ex.store.transition(r.id, RunState.SUCCEEDED)

    await _serve(store, _Ex(store, stop, slow), stop, tmp_path)
    assert beats[0] > beats[1]  # still beating after stop was set
    assert store.get(run.id).state is RunState.SUCCEEDED


async def test_heartbeat_survives_an_unexpected_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    store = RunStore(tmp_path)
    stop = asyncio.Event()
    real = store.write_heartbeat
    calls: list[int] = []

    def flaky(hb: Any) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("secret-ish detail")
        real(hb)
        stop.set()

    monkeypatch.setattr(store, "write_heartbeat", flaky)
    await _serve(store, _Ex(store, stop, None), stop, tmp_path)
    assert len(calls) >= 2 and store.read_heartbeat() is not None
    assert "secret-ish detail" not in caplog.text


async def test_serve_prunes_old_runs_at_most_once_an_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path)
    stop = asyncio.Event()
    prunes: list[str] = []
    polls = {"n": 0}
    clock = {"t": 1000.0}

    def prune(*_: Any, **__: Any) -> list[str]:
        prunes.append(threading.current_thread().name)
        raise OSError("disk")  # a failing prune never stops the loop

    def poll() -> ApplyRun | None:
        polls["n"] += 1
        clock["t"] += 2000.0  # prune checks at t=1000, 3000, 5000
        if polls["n"] >= 3:
            stop.set()
        return None

    monkeypatch.setattr(store, "prune_finished", prune)
    monkeypatch.setattr(store, "next_approved", poll)
    await asyncio.wait_for(
        serve(
            config=CFG,
            store=store,
            executor=_Ex(store, stop, None),
            overlay_root=tmp_path,
            core_version="1.50.0",
            stop=stop,
            clock=lambda: clock["t"],
        ),
        timeout=10,
    )
    assert len(prunes) == 2  # at start, then once 3600 s had passed
    assert threading.main_thread().name not in prunes
