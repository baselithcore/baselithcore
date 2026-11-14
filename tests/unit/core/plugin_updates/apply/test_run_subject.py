"""Runs carry a subject and a driver; the core claims a reserved key."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from baselith.core.plugin_updates.apply.models import (
    CORE_CLAIM,
    CORE_SUBJECT,
    RELEASING_STATES,
    ApplyRun,
    RunKind,
    RunState,
)
from baselith.core.plugin_updates.apply.store import RunConflict, RunStore

T0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
NEW_STATES = (
    RunState.PR_OPEN,
    RunState.AWAITING_MERGE,
    RunState.ROLLING_OUT,
    RunState.FINALIZING,
)


def _create(store: RunStore, plugin: str = "demo", **extra: object) -> ApplyRun:
    return store.create(
        kind=RunKind.UPDATE,
        plugin=plugin,
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256=None,
        requested_by="alice",
        approval_required=False,
        approval_ttl_seconds=3600,
        now=T0,
        **extra,  # type: ignore[arg-type]
    )


def _old_run_json(run_id: str, *, state: str = "approved") -> str:
    """A run file exactly as the release before ``subject``/``driver`` wrote it."""
    return json.dumps(
        {
            "id": run_id,
            "kind": "update",
            "plugin": "demo",
            "from_version": "1.1.0",
            "to_version": "1.2.0",
            "tarball_sha256": None,
            "requested_by": "alice",
            "requested_at": T0.isoformat(),
            "approved_by": None,
            "approval_expires_at": None,
            "state": state,
            "message": "",
            "failure": None,
            "previous_target": None,
            "target": None,
            "must_stay_active": None,
            "updated_at": T0.isoformat(),
        }
    )


def test_old_run_file_loads(tmp_path: Path) -> None:
    run_id = "pinstall-20261113T120000Z-0000abcd"
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / f"{run_id}.json").write_text(_old_run_json(run_id), encoding="utf-8")
    run = RunStore(tmp_path).get(run_id)
    assert run is not None
    assert run.subject is None
    assert run.driver == "host"
    assert run.subject_key == "plugin:demo"


def test_new_fields_round_trip(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, driver="host")
    again = store.get(run.id)
    assert again == run and again.subject is None and again.driver == "host"


def test_core_run_uses_reserved_claim(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, plugin=CORE_CLAIM, subject=CORE_SUBJECT)
    assert run.subject == "core" and run.subject_key == "core"
    assert store.active(CORE_CLAIM) == run
    with pytest.raises(RunConflict) as err:
        _create(store, plugin=CORE_CLAIM, subject=CORE_SUBJECT)
    assert err.value.active_run_id == run.id


def test_reserved_claim_refused_for_plugins(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    with pytest.raises(ValueError):
        _create(store, plugin=CORE_CLAIM, subject=None)
    with pytest.raises(ValueError):
        _create(store, plugin=CORE_CLAIM, subject=f"plugin:{CORE_CLAIM}")
    assert store.active(CORE_CLAIM) is None


@pytest.mark.parametrize(
    "subject", ["core", "plugin:other", "plugin:", "distribution", ""]
)
def test_subject_must_match_the_plugin(tmp_path: Path, subject: str) -> None:
    with pytest.raises(ValueError):
        _create(RunStore(tmp_path), plugin="demo", subject=subject)


def test_explicit_plugin_subject_is_accepted(tmp_path: Path) -> None:
    run = _create(RunStore(tmp_path), subject="plugin:demo")
    assert run.subject_key == "plugin:demo"


def test_new_states_are_not_releasing(tmp_path: Path) -> None:
    for state in NEW_STATES:
        assert state not in RELEASING_STATES
    store = RunStore(tmp_path)
    run = _create(store)
    finalizing = store.transition(run.id, RunState.FINALIZING)
    assert finalizing.state is RunState.FINALIZING
    assert store.active("demo") == finalizing  # the claim is kept
    assert [r.id for r in store.unfinished()] == [run.id]


def test_subject_and_driver_are_immutable(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    with pytest.raises(ValueError):
        store.transition(run.id, RunState.PREPARING, subject="core")
    with pytest.raises(ValueError):
        store.transition(run.id, RunState.PREPARING, driver="gitops")


def test_unknown_state_in_a_listing_does_not_crash(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    valid = _create(store)
    future_id = "pinstall-20261113T120001Z-0000beef"
    (tmp_path / "runs" / f"{future_id}.json").write_text(
        _old_run_json(future_id, state="from_the_future"), encoding="utf-8"
    )
    assert [r.id for r in store.runs()] == [valid.id]
    assert [r.id for r in store.unfinished()] == [valid.id]
    assert store.get(future_id) is None
