"""Outcome-audit claims, re-audit after an operator resolves, and run retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.plugin_updates.apply.models import RunKind, RunState
from core.plugin_updates.apply.store import RunStore

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _finished(
    store: RunStore,
    state: RunState = RunState.FAILED,
    *,
    at: datetime = T0,
    plugin: str = "demo",
    kind: RunKind = RunKind.UPDATE,
) -> str:
    run = store.create(
        kind=kind,
        plugin=plugin,
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256="f" * 64,
        requested_by="a",
        approval_required=False,
        approval_ttl_seconds=60,
        now=at,
    )
    return store.transition(run.id, state, now=at).id


def test_claim_is_exclusive_until_released(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    rid = _finished(store)
    assert store.claim_audit(rid, now=T0) is True
    assert store.claim_audit(rid, now=T0 + timedelta(minutes=1)) is False
    store.release_audit(rid)
    assert store.claim_audit(rid, now=T0 + timedelta(minutes=1)) is True


def test_a_stale_claim_is_taken_over(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    rid = _finished(store)
    assert store.claim_audit(rid, now=T0)
    assert not store.claim_audit(rid, now=T0 + timedelta(minutes=4))
    assert store.claim_audit(rid, now=T0 + timedelta(minutes=6))


def test_mark_audited_ends_the_claim_and_blocks_new_ones(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    rid = _finished(store)
    assert store.claim_audit(rid, now=T0)
    assert store.mark_audited(rid) is True
    assert store.is_audited(rid)
    assert not (tmp_path / "auditing" / rid).exists()
    assert store.claim_audit(rid, now=T0 + timedelta(hours=1)) is False
    assert store.mark_audited(rid) is False


def test_resolving_a_rollback_failed_run_reopens_its_audit(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    rid = _finished(store, RunState.ROLLBACK_FAILED)
    assert store.claim_audit(rid, now=T0) and store.mark_audited(rid)
    store.transition(rid, RunState.FAILED, actor="op", expect=RunState.ROLLBACK_FAILED)
    assert not store.is_audited(rid)
    assert store.claim_audit(rid, now=T0)


def test_other_transitions_keep_the_audited_mark(tmp_path: Path) -> None:
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
    )
    store.mark_audited(run.id)  # e.g. a request audited early
    store.transition(run.id, RunState.DENIED)
    assert store.is_audited(run.id)


def test_prune_removes_old_audited_finished_runs_only(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    old = _finished(store, at=T0 - timedelta(days=100), plugin="a")
    recent = _finished(store, at=T0 - timedelta(days=10), plugin="b")
    unaudited = _finished(store, at=T0 - timedelta(days=100), plugin="c")
    stuck = _finished(store, RunState.ROLLBACK_FAILED, at=T0 - timedelta(days=200))
    for rid in (old, recent, stuck):
        store.mark_audited(rid)
    removed = store.prune_finished(now=T0)
    assert removed == [old]
    assert store.get(old) is None and store.journal(old) == []
    assert not (tmp_path / "audited" / old).exists()
    assert not (tmp_path / "runs" / f"{old}.log.jsonl").exists()
    for rid in (recent, unaudited, stuck):
        assert store.get(rid) is not None


def test_prune_takes_unaudited_runs_after_a_year(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    ancient = _finished(store, at=T0 - timedelta(days=400))
    assert store.prune_finished(now=T0) == [ancient]


def test_prune_keeps_the_newest_succeeded_update_the_rollback_needs(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path)
    older = _finished(store, RunState.SUCCEEDED, at=T0 - timedelta(days=300))
    newest = _finished(store, RunState.SUCCEEDED, at=T0 - timedelta(days=200))
    for rid in (older, newest):
        store.mark_audited(rid)
    assert store.prune_finished(now=T0) == [older]
    last = store.last_succeeded_update("demo")
    assert last is not None and last.id == newest
