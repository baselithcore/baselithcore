from __future__ import annotations

import multiprocessing as mp
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.plugin_updates.apply.models import RunKind, RunState
from core.plugin_updates.apply.store import RunConflict, RunStateConflict, RunStore

T0 = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _create(store: RunStore, plugin: str = "demo", *, approval: bool = True, now=T0):
    return store.create(
        kind=RunKind.UPDATE,
        plugin=plugin,
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256="f" * 64,
        requested_by="alice",
        approval_required=approval,
        approval_ttl_seconds=3600,
        now=now,
    )


def _proc_create(root: str, queue) -> None:
    try:
        queue.put(("ok", _create(RunStore(Path(root))).id))
    except RunConflict as exc:
        queue.put(("conflict", exc.active_run_id))


def test_create_awaits_approval_and_claims(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    assert run.id.startswith("pinstall-20260930T120000Z-")
    assert run.state is RunState.AWAITING_APPROVAL
    assert run.approval_expires_at == T0 + timedelta(hours=1)
    assert store.active("demo") == run


def test_without_approval_starts_approved(tmp_path: Path) -> None:
    run = _create(RunStore(tmp_path), approval=False)
    assert run.state is RunState.APPROVED and run.approval_expires_at is None


def test_second_create_conflicts(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    first = _create(store)
    with pytest.raises(RunConflict) as err:
        _create(store)
    assert err.value.active_run_id == first.id
    _create(store, plugin="other")  # another plugin is independent


def test_concurrent_creates_one_wins(tmp_path: Path) -> None:
    results: list[object] = []

    def go() -> None:
        try:
            results.append(_create(RunStore(tmp_path)))
        except RunConflict as exc:
            results.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(not isinstance(r, RunConflict) for r in results) == 1


def test_concurrent_creates_across_processes_one_wins(tmp_path: Path) -> None:
    ctx = mp.get_context("spawn")
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=_proc_create, args=(str(tmp_path), queue)) for _ in range(6)
    ]
    for p in procs:
        p.start()
    outcomes = [queue.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(timeout=30)
    winners = [o for o in outcomes if o[0] == "ok"]
    assert len(winners) == 1
    assert {o[1] for o in outcomes if o[0] == "conflict"} == {winners[0][1]}


def test_terminal_state_releases_the_claim_and_journals(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, approval=False)
    store.transition(run.id, RunState.PREPARING)
    store.transition(
        run.id, RunState.FAILED, failure="release_changed", message="changed"
    )
    assert store.active("demo") is None
    assert [t.state for t in store.journal(run.id)] == [
        RunState.APPROVED,
        RunState.PREPARING,
        RunState.FAILED,
    ]
    _create(store)  # a new run may start


def test_transition_sets_fields(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, approval=False)
    out = store.transition(
        run.id, RunState.FAILED, failure="health_failed", previous_target="/x"
    )
    assert out.failure == "health_failed" and out.previous_target == "/x"
    assert store.get(run.id) == out


def test_rollback_failed_keeps_the_claim(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, approval=False)
    store.transition(run.id, RunState.ROLLBACK_FAILED)
    with pytest.raises(RunConflict):
        _create(store)


def test_expire_stale_approvals(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    assert store.expire_stale(now=T0 + timedelta(minutes=30)) == []
    expired = store.expire_stale(now=T0 + timedelta(hours=2))
    assert [r.id for r in expired] == [run.id]
    assert store.get(run.id).state is RunState.EXPIRED and store.active("demo") is None


def test_expire_does_not_clobber_a_concurrent_approval(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    store.transition(run.id, RunState.APPROVED, actor="bob")
    assert store.expire_stale(now=T0 + timedelta(hours=2)) == []
    assert store.get(run.id).state is RunState.APPROVED


def test_pending_approvals_excludes_expired(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    _create(store)
    assert len(store.pending_approvals(now=T0 + timedelta(minutes=1))) == 1
    assert store.pending_approvals(now=T0 + timedelta(hours=2)) == []


def test_history_is_newest_first_and_per_plugin(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    a = _create(store, approval=False)
    store.transition(a.id, RunState.FAILED)
    b = _create(store, approval=False, now=T0 + timedelta(minutes=1))
    _create(store, plugin="other")
    assert [r.id for r in store.history("demo")] == [b.id, a.id]


def test_mark_audited_once(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    assert store.mark_audited(run.id) is True
    assert store.mark_audited(run.id) is False


def test_is_audited_follows_mark_audited(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    assert store.is_audited(run.id) is False
    store.mark_audited(run.id)
    assert store.is_audited(run.id) is True
    assert store.is_audited("not-a-run") is False


def test_runs_lists_every_plugin_oldest_first_and_filters(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    a = _create(store, approval=False)
    b = _create(store, plugin="other", now=T0 + timedelta(seconds=5))
    store.transition(a.id, RunState.SUCCEEDED)
    assert [r.id for r in store.runs()] == [a.id, b.id]
    assert [r.id for r in store.runs({RunState.SUCCEEDED})] == [a.id]
    assert RunStore(tmp_path / "missing").runs() == []


def test_rollback_failed_event_is_public() -> None:
    from core.plugin_updates.apply import ROLLBACK_FAILED_EVENT, rollback_failed_payload

    assert ROLLBACK_FAILED_EVENT == "plugin.update_rollback_failed"
    assert callable(rollback_failed_payload)


def test_next_approved_is_oldest(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    first = _create(store, approval=False)
    _create(store, plugin="other", approval=False, now=T0 + timedelta(seconds=5))
    assert store.next_approved().id == first.id


def test_unfinished_lists_non_releasing(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    a = _create(store, approval=False)
    b = _create(store, plugin="other", approval=False)
    store.transition(b.id, RunState.ROLLBACK_FAILED)
    c = _create(store, plugin="third", approval=False)
    store.transition(c.id, RunState.SUCCEEDED)
    assert {r.id for r in store.unfinished()} == {a.id, b.id}


def test_stale_claim_of_a_finished_run_is_replaced(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, approval=False)
    store.transition(run.id, RunState.FAILED)
    (tmp_path / "active" / "demo").write_text(run.id)  # crash left the claim behind
    assert store.active("demo") is None
    _create(store)


def test_heartbeat_and_expectations_roundtrip(tmp_path: Path) -> None:
    from core.plugin_updates.apply.models import Expectation, UpdaterHeartbeat

    store = RunStore(tmp_path)
    assert store.read_heartbeat() is None
    hb = UpdaterHeartbeat(
        pid=1,
        started_at=T0,
        at=T0,
        core_version="1",
        enabled=True,
        overlay_root=None,
        overlay_writable=False,
        restart_configured=True,
    )
    store.write_heartbeat(hb)
    assert store.read_heartbeat() == hb
    exp = Expectation(
        run_id="pinstall-20260930T120000Z-abcd1234",
        plugin="demo",
        version="1.2.0",
        store_dir=None,
        restart_at=T0,
        must_stay_active=["auth"],
    )
    store.write_expectation(exp)
    assert store.read_expectation("demo") == exp and store.expected_plugins() == [
        "demo"
    ]
    store.clear_expectation("demo")
    assert store.read_expectation("demo") is None and store.expected_plugins() == []


def test_run_ids_are_path_safe(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    assert store.get("../../etc/passwd") is None
    with pytest.raises(ValueError):
        store.transition("../x", RunState.FAILED)
    with pytest.raises(ValueError):
        store.active("../x")


def test_expire_then_approve_conflicts(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    store.expire_stale(now=T0 + timedelta(hours=2))
    with pytest.raises(RunStateConflict) as err:
        store.transition(
            run.id,
            RunState.APPROVED,
            expect=RunState.AWAITING_APPROVAL,
            approved_by="bob",
        )
    assert err.value.actual is RunState.EXPIRED
    assert store.get(run.id).state is RunState.EXPIRED
    assert store.next_approved() is None


def test_second_approval_conflicts(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    store.transition(run.id, RunState.APPROVED, expect=RunState.AWAITING_APPROVAL)
    with pytest.raises(RunStateConflict):
        store.transition(run.id, RunState.APPROVED, expect=RunState.AWAITING_APPROVAL)


@pytest.mark.parametrize(
    "final", [RunState.DENIED, RunState.SUCCEEDED, RunState.FAILED]
)
def test_terminal_runs_cannot_be_resurrected(tmp_path: Path, final: RunState) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    store.transition(run.id, final)
    with pytest.raises(RunStateConflict):
        store.transition(run.id, RunState.APPROVED)
    assert store.get(run.id).state is final and store.next_approved() is None
    assert len(store.journal(run.id)) == 2


def test_expect_accepts_a_collection(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, approval=False)
    store.transition(
        run.id, RunState.PREPARING, expect={RunState.APPROVED, RunState.PREPARING}
    )


def test_immutable_and_unknown_fields_refused(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store)
    for bad in ("id", "plugin", "kind", "requested_by", "requested_at", "bogus"):
        with pytest.raises(ValueError):
            store.transition(run.id, RunState.APPROVED, **{bad: "x"})
    assert store.get(run.id).state is RunState.AWAITING_APPROVAL


def test_torn_journal_line_is_skipped_and_not_glued(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, approval=False)
    log = tmp_path / "runs" / f"{run.id}.log.jsonl"
    with log.open("a") as fh:
        fh.write('{"at": "2026-09-30T12:00')  # crash mid-append, no newline
    assert [t.state for t in store.journal(run.id)] == [RunState.APPROVED]
    store.transition(run.id, RunState.PREPARING)
    assert [t.state for t in store.journal(run.id)] == [
        RunState.APPROVED,
        RunState.PREPARING,
    ]


def test_corrupt_snapshot_reads_as_missing(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, approval=False)
    (tmp_path / "runs" / f"{run.id}.json").write_text('{"id": "trunc')
    assert store.get(run.id) is None
    assert store.active("demo") is None
    assert store.next_approved() is None and store.unfinished() == []
    assert store.history("demo") == []


def test_next_approved_requires_the_live_claim(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run = _create(store, approval=False)
    assert store.next_approved().id == run.id and [
        r.id for r in store.unfinished()
    ] == [run.id]
    (tmp_path / "active" / "demo").unlink()  # claim lost
    assert store.next_approved() is None and store.unfinished() == []


def test_clear_stale_expectations_drops_only_finished_runs(tmp_path: Path) -> None:
    from core.plugin_updates.apply.models import Expectation

    store = RunStore(tmp_path)
    done = _create(store, approval=False)
    store.transition(done.id, RunState.SUCCEEDED)
    live = store.create(
        kind=RunKind.UPDATE,
        plugin="auth",
        from_version="1",
        to_version="2",
        tarball_sha256="a" * 64,
        requested_by="x",
        approval_required=False,
        approval_ttl_seconds=60,
    )
    for plugin, run_id in (
        ("demo", done.id),
        ("auth", live.id),
        ("ghost", "pinstall-20260930T120000Z-00000000"),
    ):
        store.write_expectation(
            Expectation(
                run_id=run_id,
                plugin=plugin,
                version=None,
                store_dir=None,
                restart_at=T0,
                must_stay_active=[],
            )
        )
    assert store.clear_stale_expectations() == ["demo", "ghost"]
    assert store.expected_plugins() == ["auth"]
