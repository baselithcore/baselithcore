"""Crash reconciliation at updater start (spec §4.5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from core.plugin_updates.apply import _events
from core.plugin_updates.apply.models import Expectation, RunKind, RunState
from core.plugin_updates.apply.reconcile import reconcile
from core.plugin_updates.apply.store import RunStore

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


class _Exec:
    def __init__(self, store: RunStore, overlay_root: Path | None = None) -> None:
        self.store, self.calls = store, []
        if overlay_root is not None:
            self.overlay_root = overlay_root
        self.undo_error: Exception | None = None
        self.rollback_ends = RunState.ROLLED_BACK

    async def resume_activation(self, run):
        self.calls.append(("resume", run.id))
        return self.store.transition(run.id, RunState.SUCCEEDED)

    async def redo_rollback(self, run):
        self.calls.append(("rollback", run.id))
        return self.store.transition(run.id, self.rollback_ends)

    def undo_swap(self, run) -> None:
        self.calls.append(("undo", run.id))
        if self.undo_error is not None:
            raise self.undo_error


def _run(store: RunStore, state: RunState, plugin: str = "demo"):
    run = store.create(
        kind=RunKind.UPDATE,
        plugin=plugin,
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256="f" * 64,
        requested_by="a",
        approval_required=False,
        approval_ttl_seconds=60,
        now=T0,
    )
    return store.transition(run.id, state, previous_target=None, target="demo-1.2.0")


@pytest.fixture()
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    seen: list[tuple[str, dict[str, Any]]] = []

    class _Bus:
        async def emit(self, name: str, data: dict[str, Any], **_: Any) -> int:
            seen.append((name, data))
            return 1

    monkeypatch.setattr(_events, "get_event_bus", lambda: _Bus())
    return seen


async def test_reconcile_resumes_activating_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _run(store, RunState.ACTIVATING)
    ex = _Exec(store)
    assert await reconcile(store, ex, now=T0) == [run.id]
    assert ex.calls == [("resume", run.id)]
    assert store.get(run.id).state is RunState.SUCCEEDED


async def test_health_checking_is_resumed_too(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _run(store, RunState.HEALTH_CHECKING)
    ex = _Exec(store)
    await reconcile(store, ex, now=T0)
    assert ex.calls == [("resume", run.id)]


async def test_migrating_is_undone_and_failed(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _run(store, RunState.MIGRATING)
    ex = _Exec(store)
    await reconcile(store, ex, now=T0)
    assert ex.calls == [("undo", run.id)]
    assert store.get(run.id).failure == "interrupted"
    assert store.get(run.id).state is RunState.FAILED


async def test_migrating_removes_its_schema_scratch(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "state")
    run = _run(store, RunState.MIGRATING)
    overlay = tmp_path / "ov"
    scratch = overlay / ".store" / f".staging-{run.id}-schema-abc"
    (scratch / "x").mkdir(parents=True)
    await reconcile(store, _Exec(store, overlay_root=overlay), now=T0)
    done = store.get(run.id)
    assert done.state is RunState.FAILED and not scratch.exists()
    assert "nothing changed on the running API" in done.message


async def test_migrating_whose_undo_fails_is_rollback_failed(
    tmp_path: Path, events: list[tuple[str, dict[str, Any]]]
) -> None:
    store = RunStore(tmp_path)
    run = _run(store, RunState.MIGRATING)
    ex = _Exec(store)
    ex.undo_error = OSError(f"{tmp_path}/ov/demo")
    await reconcile(store, ex, now=T0)
    done = store.get(run.id)
    assert done.state is RunState.ROLLBACK_FAILED and done.failure == "interrupted"
    assert str(tmp_path) not in done.message
    assert store.active("demo") is not None
    assert [n for n, _ in events] == ["plugin.update_rollback_failed"]


async def test_preparing_cleans_staging_and_fails(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _run(store, RunState.PREPARING)
    await reconcile(store, _Exec(store), now=T0)
    assert store.get(run.id).state is RunState.FAILED
    assert store.get(run.id).failure == "interrupted"
    assert store.active("demo") is None


async def test_preparing_removes_its_staging_dirs_only(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "state")
    run = _run(store, RunState.PREPARING)
    overlay = tmp_path / "ov"
    mine = overlay / ".store" / f".staging-{run.id}"
    scratch = overlay / ".store" / f".staging-{run.id}-verify-abc"
    other = overlay / ".store" / ".staging-pinstall-20260101T000000Z-00000000"
    for d in (mine, scratch, other):
        (d / "x").mkdir(parents=True)
    await reconcile(store, _Exec(store, overlay_root=overlay), now=T0)
    assert not mine.exists() and not scratch.exists() and other.exists()


async def test_rolling_back_is_redone(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _run(store, RunState.ROLLING_BACK)
    ex = _Exec(store)
    await reconcile(store, ex, now=T0)
    assert ex.calls == [("rollback", run.id)]


async def test_a_redone_rollback_that_fails_emits_the_event(
    tmp_path: Path, events: list[tuple[str, dict[str, Any]]]
) -> None:
    store = RunStore(tmp_path)
    run = _run(store, RunState.ROLLING_BACK)
    ex = _Exec(store)
    ex.rollback_ends = RunState.ROLLBACK_FAILED
    await reconcile(store, ex, now=T0)
    assert events == [
        (
            "plugin.update_rollback_failed",
            {
                "run_id": run.id,
                "plugin": "demo",
                "kind": "update",
                "from_version": "1.1.0",
                "to_version": "1.2.0",
                "failure": None,
            },
        )
    ]


async def test_approved_and_rollback_failed_are_left_alone(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    a = _run(store, RunState.APPROVED)
    b = _run(store, RunState.ROLLBACK_FAILED, plugin="other")
    ex = _Exec(store)
    await reconcile(store, ex, now=T0 + timedelta(days=2))
    assert ex.calls == [] and store.get(a.id).state is RunState.APPROVED
    assert store.get(b.id).state is RunState.ROLLBACK_FAILED


async def test_stale_approvals_expire_first(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = store.create(
        kind=RunKind.UPDATE,
        plugin="demo",
        from_version="1",
        to_version="2",
        tarball_sha256="f" * 64,
        requested_by="a",
        approval_required=True,
        approval_ttl_seconds=60,
        now=T0,
    )
    assert await reconcile(store, _Exec(store), now=T0 + timedelta(minutes=2)) == []
    assert store.get(run.id).state is RunState.EXPIRED


async def test_stale_expectations_are_cleared(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _run(store, RunState.ACTIVATING)
    store.transition(run.id, RunState.SUCCEEDED)
    store.write_expectation(
        Expectation(
            run_id=run.id,
            plugin="demo",
            version="1.2.0",
            store_dir=None,
            restart_at=T0,
            must_stay_active=[],
        )
    )
    await reconcile(store, _Exec(store), now=T0)
    assert store.read_expectation("demo") is None


async def test_one_failing_resume_does_not_strand_the_others(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    a = _run(store, RunState.ACTIVATING, plugin="alpha")
    b = _run(store, RunState.PREPARING, plugin="beta")
    stale = _run(store, RunState.ACTIVATING, plugin="gamma")
    store.transition(stale.id, RunState.SUCCEEDED)
    store.write_expectation(
        Expectation(
            run_id=stale.id,
            plugin="gamma",
            version=None,
            store_dir=None,
            restart_at=T0,
            must_stay_active=[],
        )
    )
    ex = _Exec(store)

    async def boom(run):  # type: ignore[no-untyped-def]
        raise KeyError("resume")

    ex.resume_activation = boom  # type: ignore[method-assign]
    with pytest.raises(KeyError):
        await reconcile(store, ex, now=T0)
    assert store.get(a.id).state is RunState.ACTIVATING
    assert store.get(b.id).state is RunState.FAILED
    assert store.read_expectation("gamma") is None
