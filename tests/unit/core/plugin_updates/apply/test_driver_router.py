"""The driver router: each run goes to the driver its subject and driver name pick."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from baselith.core.plugin_updates.apply.drivers import (
    CORE_HOST_DRIVER,
    PLUGIN_OVERLAY_DRIVER,
    ApplyDriver,
    DriverRegistry,
    DriverRouter,
    UnknownDriver,
    get_driver,
    register_driver,
    registered_drivers,
)
from baselith.core.plugin_updates.apply.executor import Executor
from baselith.core.plugin_updates.apply.models import (
    CORE_CLAIM,
    CORE_SUBJECT,
    ApplyRun,
    RunKind,
    RunState,
)
from baselith.core.plugin_updates.apply.reconcile import reconcile
from baselith.core.plugin_updates.apply.store import RunStore

T0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@dataclass
class Recorder:
    """A driver that records every call and finishes the run as succeeded."""

    store: RunStore
    name: str
    calls: list[tuple[str, str]] = field(default_factory=list)
    overlay_root: Path | None = None

    async def _finish(self, method: str, run: ApplyRun) -> ApplyRun:
        self.calls.append((method, run.id))
        return self.store.transition(run.id, RunState.SUCCEEDED)

    async def execute(self, run: ApplyRun) -> ApplyRun:
        return await self._finish("execute", run)

    async def resume_activation(self, run: ApplyRun) -> ApplyRun:
        return await self._finish("resume_activation", run)

    async def redo_rollback(self, run: ApplyRun) -> ApplyRun:
        return await self._finish("redo_rollback", run)

    def undo_swap(self, run: ApplyRun) -> None:
        self.calls.append(("undo_swap", run.id))


def _create(
    store: RunStore,
    *,
    plugin: str = "demo",
    subject: str | None = None,
    driver: str = "host",
) -> ApplyRun:
    return store.create(
        kind=RunKind.UPDATE,
        plugin=plugin,
        from_version="1.0.0",
        to_version="1.1.0",
        tarball_sha256=None,
        requested_by="alice",
        approval_required=False,
        approval_ttl_seconds=60,
        now=T0,
        subject=subject,
        driver=driver,
    )


def _router(
    store: RunStore, *, core: bool = True
) -> tuple[DriverRouter, Recorder, Recorder]:
    registry = DriverRegistry()
    plugin = Recorder(store, PLUGIN_OVERLAY_DRIVER, overlay_root=Path("/overlay"))
    core_driver = Recorder(store, CORE_HOST_DRIVER)
    registry.register(PLUGIN_OVERLAY_DRIVER, lambda: plugin)
    if core:
        registry.register(CORE_HOST_DRIVER, lambda: core_driver)
    return DriverRouter(store, resolve=registry.get), plugin, core_driver


async def test_plugin_run_goes_to_the_overlay_driver(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    router, plugin, core = _router(store)
    run = _create(store)
    done = await router.execute(run)
    assert done.state is RunState.SUCCEEDED
    assert plugin.calls == [("execute", run.id)] and core.calls == []


async def test_core_run_goes_to_the_core_driver(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    router, plugin, core = _router(store)
    run = _create(store, plugin=CORE_CLAIM, subject=CORE_SUBJECT)
    await router.execute(run)
    assert core.calls == [("execute", run.id)] and plugin.calls == []


async def test_unknown_driver_run_is_failed_not_executed(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    router, plugin, core = _router(store)
    run = _create(store, driver="gitops")
    done = await router.execute(run)
    assert plugin.calls == [] and core.calls == []
    assert done.state is RunState.FAILED and done.failure == "unknown_driver"
    assert store.active("demo") is None  # the claim is released


async def test_core_run_without_a_core_driver_is_failed(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    router, plugin, _ = _router(store, core=False)
    run = _create(store, plugin=CORE_CLAIM, subject=CORE_SUBJECT)
    done = await router.execute(run)
    assert plugin.calls == []
    assert done.state is RunState.FAILED
    assert done.failure == "core_driver_unavailable"


async def test_a_run_another_process_moved_is_left_alone(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    router, _, _ = _router(store)
    run = _create(store, driver="gitops")
    store.transition(run.id, RunState.PREPARING)
    done = await router.execute(run)  # the stale snapshot still says approved
    assert done.state is RunState.PREPARING


@pytest.mark.parametrize(
    ("state", "method"),
    [
        (RunState.ACTIVATING, "resume_activation"),
        (RunState.HEALTH_CHECKING, "resume_activation"),
        (RunState.ROLLING_BACK, "redo_rollback"),
    ],
)
async def test_recovery_methods_route_the_same_way(
    tmp_path: Path, state: RunState, method: str
) -> None:
    store = RunStore(tmp_path)
    router, plugin, core = _router(store)
    plugin_run = store.transition(_create(store).id, state)
    core_run = store.transition(
        _create(store, plugin=CORE_CLAIM, subject=CORE_SUBJECT).id, state
    )
    await reconcile(store, router)
    assert plugin.calls == [(method, plugin_run.id)]
    assert core.calls == [(method, core_run.id)]


async def test_undo_swap_routes_by_subject(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    router, plugin, core = _router(store)
    run = _create(store, plugin=CORE_CLAIM, subject=CORE_SUBJECT)
    router.undo_swap(run)
    assert core.calls == [("undo_swap", run.id)] and plugin.calls == []


async def test_recovery_without_its_driver_needs_an_operator(tmp_path: Path) -> None:
    """Nothing can tell what an interrupted run left: keep the claim, ask a person."""
    store = RunStore(tmp_path)
    router, plugin, _ = _router(store)
    run = store.transition(_create(store, driver="gitops").id, RunState.ACTIVATING)
    await reconcile(store, router)
    after = store.get(run.id)
    assert plugin.calls == []
    assert after is not None and after.state is RunState.ROLLBACK_FAILED
    assert after.failure == "unknown_driver"
    assert store.active("demo") == after


async def test_interrupted_migration_without_its_driver_needs_an_operator(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path)
    router, _, _ = _router(store, core=False)
    run = _create(store, plugin=CORE_CLAIM, subject=CORE_SUBJECT)
    store.transition(run.id, RunState.MIGRATING)
    await reconcile(store, router)
    after = store.get(run.id)
    assert after is not None and after.state is RunState.ROLLBACK_FAILED


def test_overlay_root_is_the_plugin_drivers(tmp_path: Path) -> None:
    router, _, _ = _router(RunStore(tmp_path))
    assert router.overlay_root == Path("/overlay")


def test_overlay_root_is_none_without_a_plugin_driver(tmp_path: Path) -> None:
    router = DriverRouter(RunStore(tmp_path), resolve=DriverRegistry().get)
    assert router.overlay_root is None


def test_registry_refuses_an_unknown_name_with_a_value_error() -> None:
    registry = DriverRegistry()
    with pytest.raises(UnknownDriver) as err:
        registry.get("nope")
    assert isinstance(err.value, ValueError)
    assert not isinstance(err.value, KeyError)


def test_registry_lists_and_replaces(tmp_path: Path) -> None:
    registry = DriverRegistry()
    first = Recorder(RunStore(tmp_path), "a")
    second = Recorder(RunStore(tmp_path), "b")
    registry.register("a", lambda: first)
    registry.register("a", lambda: second)
    assert registry.names() == ("a",)
    assert registry.get("a") is second


def test_module_registry_functions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from baselith.core.plugin_updates.apply.drivers import registry as registry_mod

    monkeypatch.setattr(registry_mod, "_DEFAULT", DriverRegistry())
    driver = Recorder(RunStore(tmp_path), "test-only")
    register_driver("test-only-driver", lambda: driver)
    assert "test-only-driver" in registered_drivers()
    assert get_driver("test-only-driver") is driver


def test_the_executor_is_an_apply_driver(tmp_path: Path) -> None:
    """Structural: the existing executor satisfies the protocol unedited."""
    store = RunStore(tmp_path)
    executor: ApplyDriver = Executor.__new__(Executor)
    for method in ("execute", "resume_activation", "redo_rollback", "undo_swap"):
        assert callable(getattr(executor, method))
    _: ApplyDriver = Recorder(store, "x")
