"""The updater and the core: the core driver's wiring and the self-restart at the end."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from baselith.core.plugin_updates.apply import updater as updater_mod
from baselith.core.plugin_updates.apply.models import ApplyRun, RunKind, RunState
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.apply.updater import build_executor, serve

from .test_updater import CFG, _create, _Ex


@pytest.fixture(autouse=True)
def _no_plugin_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater_mod, "_plugin_code_loaded", lambda: False)


def test_core_driver_registered_only_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from baselith.core.plugin_updates.apply.drivers import CoreHostDriver
    from baselith.core.plugin_updates.apply.models import CORE_CLAIM, CORE_SUBJECT

    monkeypatch.setenv("BASELITH_PLUGIN_OVERLAY_DIR", str(tmp_path))
    cfg = CFG.model_copy(update={"state_dir": tmp_path / "s"})
    off, _ = build_executor(cfg)
    on, _ = build_executor(cfg.model_copy(update={"core_enabled": True}))
    store = RunStore(tmp_path / "s")
    run = store.create(
        kind=RunKind.UPDATE,
        plugin=CORE_CLAIM,
        subject=CORE_SUBJECT,
        from_version="1",
        to_version="2",
        tarball_sha256="a" * 40,
        requested_by="a",
        approval_required=False,
        approval_ttl_seconds=60,
    )
    assert isinstance(on._driver_for(run), CoreHostDriver)  # type: ignore[attr-defined]
    with pytest.raises(Exception, match="no core-host driver"):
        off._driver_for(run)  # type: ignore[attr-defined]


def test_build_executor_without_overlay_when_core_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BASELITH_PLUGIN_OVERLAY_DIR", raising=False)
    cfg = CFG.model_copy(update={"state_dir": tmp_path / "s", "core_enabled": True})
    ex, root = build_executor(cfg)
    assert root is None and ex.overlay_root is None  # type: ignore[attr-defined]


async def test_serve_runs_updater_restart_command_after_finalizing(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    stop = asyncio.Event()
    seen: list[tuple[list[str], RunState | None]] = []

    async def finalizing(ex: _Ex, r: ApplyRun) -> ApplyRun:
        ex.store.transition(r.id, RunState.PREPARING)
        return ex.store.transition(r.id, RunState.FINALIZING)

    def runner(argv: Any, *, env: Any = None, timeout: float) -> int:
        current = store.get(run.id)
        seen.append((list(argv), current.state if current else None))
        return 0

    cfg = CFG.model_copy(
        update={"updater_restart_command": ["sudo", "-n", "systemctl", "restart", "u"]}
    )
    await asyncio.wait_for(
        serve(
            config=cfg,
            store=store,
            executor=_Ex(store, stop, finalizing),
            overlay_root=tmp_path,
            core_version="1.50.0",
            stop=stop,
            runner=runner,
        ),
        timeout=10,
    )
    assert seen == [(["sudo", "-n", "systemctl", "restart", "u"], RunState.FINALIZING)]
    assert store.get(run.id).state is RunState.FINALIZING  # closed by reconcile later


async def test_finalizing_without_restart_command_waits(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    stop = asyncio.Event()
    calls: list[Any] = []

    async def finalizing(ex: _Ex, r: ApplyRun) -> ApplyRun:
        ex.store.transition(r.id, RunState.PREPARING)
        ex.stop.set()
        return ex.store.transition(r.id, RunState.FINALIZING)

    await asyncio.wait_for(
        serve(
            config=CFG,
            store=store,
            executor=_Ex(store, stop, finalizing),
            overlay_root=tmp_path,
            core_version="1.50.0",
            stop=stop,
            runner=lambda *a, **k: calls.append(a) or 0,
        ),
        timeout=10,
    )
    assert calls == [] and store.get(run.id).state is RunState.FINALIZING


async def test_serve_settles_a_finalizing_run_past_the_timeout(tmp_path: Path) -> None:
    """The updater could not restart itself: the loop still ends the run in time."""
    from datetime import UTC, datetime, timedelta

    from baselith.core.plugin_updates.apply.boot_report import BootReport

    store = RunStore(tmp_path)
    run = _create(store)
    report = BootReport(
        pid=1,
        booted_at=datetime.now(UTC),
        core_version="1.3.1",
        framework_version="2",
        plugins={},
    )
    store.boot_dir.mkdir(parents=True, exist_ok=True)
    (store.boot_dir / "1.json").write_text(report.model_dump_json())
    stop = asyncio.Event()

    async def finalizing(ex: _Ex, r: ApplyRun) -> ApplyRun:
        ex.store.transition(r.id, RunState.PREPARING)
        return ex.store.transition(r.id, RunState.FINALIZING)

    cfg = CFG.model_copy(
        update={
            "updater_restart_command": ["sudo", "-n", "systemctl", "restart", "u"],
            "finalize_timeout_seconds": 60,
        }
    )
    task = asyncio.create_task(
        serve(
            config=cfg,
            store=store,
            executor=_Ex(store, stop, finalizing),
            overlay_root=tmp_path,
            core_version="1.50.0",
            stop=stop,
            runner=lambda *a, **k: 1,  # the self-restart fails
            wall_clock=lambda: datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    for _ in range(100):
        await asyncio.sleep(0.05)
        current = store.get(run.id)
        if current is not None and current.state is RunState.SUCCEEDED:
            break
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    done = store.get(run.id)
    assert done is not None and done.state is RunState.SUCCEEDED
    assert "updater" in done.message
