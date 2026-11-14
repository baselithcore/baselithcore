"""A policy run starts only inside the window it was created for (re-checked at pick-up)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from baselith.core.plugin_updates.apply import _events
from baselith.core.plugin_updates.apply import updater as updater_mod
from baselith.core.plugin_updates.apply.models import ApplyRun, RunKind, RunState
from baselith.core.plugin_updates.apply.policy_runner import POLICY_ACTOR
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.apply.updater import serve
from baselith.core.plugin_updates.policy import POLICY_OFF, UpdatePolicy

from ._policy_env import IN, OUT, WINDOW
from .test_updater import CFG, _Ex


@pytest.fixture(autouse=True)
def _no_plugin_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater_mod, "_plugin_code_loaded", lambda: False)


@pytest.fixture()
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    class _Bus:
        async def emit(self, name: str, data: dict[str, Any], **_: Any) -> int:
            seen.append((name, data))
            return 1

    monkeypatch.setattr(_events, "get_event_bus", lambda: _Bus())
    return seen


def _policy_run(store: RunStore, *, by: str = POLICY_ACTOR) -> ApplyRun:
    return store.create(
        kind=RunKind.UPDATE,
        plugin="demo",
        from_version="1.1.0",
        to_version="1.1.1",
        tarball_sha256="f" * 64,
        requested_by=by,
        approval_required=False,
        approval_ttl_seconds=60,
    )


async def _serve_once(
    store: RunStore, at: datetime, *, estimate: int = 60
) -> list[str]:
    """Serve until the queue is empty; the ids the executor was handed."""
    stop = asyncio.Event()
    executed: list[str] = []

    async def succeed(ex: _Ex, r: ApplyRun) -> ApplyRun:
        executed.append(r.id)
        ex.store.transition(r.id, RunState.PREPARING)
        return ex.store.transition(r.id, RunState.SUCCEEDED)

    cfg = CFG.model_copy(update={"policy_estimate_seconds": estimate})
    task = asyncio.create_task(
        serve(
            config=cfg,
            store=store,
            executor=_Ex(store, stop, succeed),
            overlay_root=store.root,
            core_version="1.50.0",
            stop=stop,
            wall_clock=lambda: at,
        )
    )
    for _ in range(100):
        await asyncio.sleep(0.05)
        if store.next_approved() is None:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    return executed


async def test_policy_run_inside_the_window_runs(
    tmp_path: Path, events: list[Any]
) -> None:
    store = RunStore(tmp_path)
    store.set_policy(UpdatePolicy(plugins="patch", window=WINDOW), actor="cli:op")
    run = _policy_run(store)
    assert await _serve_once(store, IN) == [run.id]


async def test_policy_run_after_the_window_closed_is_deferred_not_started(
    tmp_path: Path, events: list[Any]
) -> None:
    store = RunStore(tmp_path)
    store.set_policy(UpdatePolicy(plugins="patch", window=WINDOW), actor="cli:op")
    run = _policy_run(store)
    assert await _serve_once(store, OUT) == []
    done = store.get(run.id)
    assert done is not None and done.state is RunState.EXPIRED
    assert done.failure == "window_closed" and "next window" in done.message
    assert store.active("demo") is None  # the claim is free for the next window
    assert (
        "update.policy_deferred",
        {"code": "window_closed", "subject": "plugin:demo"},
    ) in events


async def test_policy_run_that_no_longer_fits_is_deferred(
    tmp_path: Path, events: list[Any]
) -> None:
    store = RunStore(tmp_path)
    store.set_policy(UpdatePolicy(plugins="patch", window=WINDOW), actor="cli:op")
    run = _policy_run(store)
    late = IN + timedelta(minutes=89)  # one minute left of the window
    assert await _serve_once(store, late, estimate=300) == []
    assert store.get(run.id).state is RunState.EXPIRED  # type: ignore[union-attr]


async def test_policy_run_after_the_policy_was_turned_off_is_deferred(
    tmp_path: Path, events: list[Any]
) -> None:
    store = RunStore(tmp_path)
    store.set_policy(UpdatePolicy(plugins="patch", window=WINDOW), actor="cli:op")
    run = _policy_run(store)
    store.set_policy(POLICY_OFF, actor="cli:op")
    assert await _serve_once(store, IN) == []
    done = store.get(run.id)
    assert done is not None and (done.state, done.failure) == (
        RunState.EXPIRED,
        "policy_off",
    )


async def test_a_manual_run_ignores_the_window(
    tmp_path: Path, events: list[Any]
) -> None:
    store = RunStore(tmp_path)
    store.set_policy(UpdatePolicy(plugins="patch", window=WINDOW), actor="cli:op")
    run = _policy_run(store, by="cli:op")
    assert await _serve_once(store, OUT) == [run.id]
