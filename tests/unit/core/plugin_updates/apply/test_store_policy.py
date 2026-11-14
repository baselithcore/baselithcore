"""The run store keeps the update policy and its pending proposal."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest

from baselith.core.plugin_updates.apply import PolicyRefused, RunStore
from baselith.core.plugin_updates.policy import (
    POLICY_OFF,
    MaintenanceWindow,
    UpdatePolicy,
)

T0 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)
WINDOW = MaintenanceWindow(
    days=frozenset({5, 6}), start=time(1, 0), end=time(5, 0), tz="Europe/Rome"
)
PATCH = UpdatePolicy(plugins="patch", window=WINDOW)


@pytest.fixture()
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "apply")


def _journal(store: RunStore) -> list[dict[str, object]]:
    text = (store.root / "policy.log.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def test_missing_file_is_policy_off(store: RunStore) -> None:
    assert store.get_policy() == POLICY_OFF
    assert store.pending_proposal(T0) is None


def test_corrupt_file_is_policy_off(store: RunStore) -> None:
    store.root.mkdir(parents=True)
    (store.root / "policy.json").write_text("{not json", encoding="utf-8")
    assert store.get_policy() == POLICY_OFF
    (store.root / "policy.json").write_text(
        json.dumps({"plugins": "patch"}), encoding="utf-8"
    )  # valid JSON, invalid policy (no window)
    assert store.get_policy() == POLICY_OFF


def test_set_policy_roundtrip_and_journal(store: RunStore) -> None:
    store.set_policy(PATCH, actor="cli:ops", now=T0)
    assert store.get_policy() == PATCH
    store.set_policy(POLICY_OFF, actor="cli:ops", now=T0)
    assert store.get_policy() == POLICY_OFF
    first, second = _journal(store)
    assert first["op"] == "set" and first["actor"] == "cli:ops"
    assert first["previous_sha256"] is None
    assert second["previous_sha256"] == first["sha256"]
    assert "window" not in json.dumps(first)  # the journal carries hashes, not content


def test_proposal_does_not_change_the_live_policy(store: RunStore) -> None:
    proposal = store.propose_policy(PATCH, actor="alice", ttl_seconds=600, now=T0)
    assert store.get_policy() == POLICY_OFF
    assert store.pending_proposal(T0) == proposal
    assert proposal.expires_at == T0 + timedelta(seconds=600)


def test_proposal_cannot_be_approved_by_its_proposer(store: RunStore) -> None:
    seen = store.propose_policy(PATCH, actor="alice", ttl_seconds=600, now=T0)
    with pytest.raises(PolicyRefused) as refused:
        store.approve_policy(approver="alice", now=T0, expected_digest=seen.digest)
    assert refused.value.code == "self_approval"
    assert store.get_policy() == POLICY_OFF
    assert store.pending_proposal(T0) is not None


def test_expired_proposal_is_refused(store: RunStore) -> None:
    seen = store.propose_policy(PATCH, actor="alice", ttl_seconds=600, now=T0)
    later = T0 + timedelta(seconds=601)
    assert store.pending_proposal(later) is None  # inert once expired
    with pytest.raises(PolicyRefused) as refused:
        store.approve_policy(approver="bob", now=later, expected_digest=seen.digest)
    assert refused.value.code == "expired"
    assert store.get_policy() == POLICY_OFF


def test_no_proposal_is_refused(store: RunStore) -> None:
    with pytest.raises(PolicyRefused) as refused:
        store.approve_policy(approver="bob", now=T0, expected_digest="0" * 64)
    assert refused.value.code == "no_proposal"


def test_approve_applies_and_clears_the_proposal(store: RunStore) -> None:
    proposal = store.propose_policy(PATCH, actor="alice", ttl_seconds=600, now=T0)
    live = store.approve_policy(
        approver="bob", now=T0 + timedelta(seconds=60), expected_digest=proposal.digest
    )
    assert live == PATCH and store.get_policy() == PATCH
    assert store.pending_proposal(T0) is None
    entry = _journal(store)[-1]
    assert entry["op"] == "approve"
    assert entry["actor"] == "bob" and entry["proposed_by"] == "alice"


def test_approve_refuses_a_proposal_that_changed(store: RunStore) -> None:
    seen = store.propose_policy(PATCH, actor="alice", ttl_seconds=600, now=T0)
    store.propose_policy(  # the proposer may revise their own proposal
        UpdatePolicy(core="patch", plugins="patch", window=WINDOW),
        actor="alice",
        ttl_seconds=600,
        now=T0,
    )
    with pytest.raises(PolicyRefused) as refused:
        store.approve_policy(approver="bob", now=T0, expected_digest=seen.digest)
    assert refused.value.code == "changed"
    assert store.get_policy() == POLICY_OFF


@pytest.mark.parametrize("digest", ["", "x", "0" * 64, "é" * 64])
def test_approve_without_the_seen_digest_is_refused(
    store: RunStore, digest: str
) -> None:
    store.propose_policy(PATCH, actor="alice", ttl_seconds=600, now=T0)
    with pytest.raises(PolicyRefused) as refused:
        store.approve_policy(approver="bob", now=T0, expected_digest=digest)
    assert refused.value.code == "changed"
    assert store.get_policy() == POLICY_OFF


def test_digest_is_mandatory() -> None:
    import inspect

    param = inspect.signature(RunStore.approve_policy).parameters["expected_digest"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_another_actors_live_proposal_cannot_be_replaced(store: RunStore) -> None:
    seen = store.propose_policy(PATCH, actor="alice", ttl_seconds=600, now=T0)
    with pytest.raises(PolicyRefused) as refused:
        store.propose_policy(POLICY_OFF, actor="carol", ttl_seconds=600, now=T0)
    assert refused.value.code == "pending"
    assert store.pending_proposal(T0) == seen
    store.discard_proposal(actor="carol", now=T0)  # explicit, journaled
    mine = store.propose_policy(POLICY_OFF, actor="carol", ttl_seconds=600, now=T0)
    assert store.pending_proposal(T0) == mine


def test_an_expired_proposal_can_be_replaced(store: RunStore) -> None:
    store.propose_policy(PATCH, actor="alice", ttl_seconds=600, now=T0)
    later = T0 + timedelta(seconds=601)
    mine = store.propose_policy(POLICY_OFF, actor="carol", ttl_seconds=600, now=later)
    assert store.pending_proposal(later) == mine


def test_discard_drops_the_proposal(store: RunStore) -> None:
    store.propose_policy(PATCH, actor="alice", ttl_seconds=600, now=T0)
    store.discard_proposal(actor="bob", now=T0)
    assert store.pending_proposal(T0) is None
    assert _journal(store)[-1]["op"] == "discard"
    store.discard_proposal(actor="bob", now=T0)  # idempotent


def test_set_policy_is_atomic_under_concurrent_writers(store: RunStore) -> None:
    policies = [PATCH, POLICY_OFF, UpdatePolicy(core="patch", window=WINDOW)]
    stop = threading.Event()
    bad: list[str] = []

    def write(policy: UpdatePolicy) -> None:
        for _ in range(40):
            store.set_policy(policy, actor="t")

    def read() -> None:
        path = store.root / "policy.json"
        while not stop.is_set():
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                continue
            try:
                UpdatePolicy.model_validate_json(text)
            except ValueError:
                bad.append(text)

    reader = threading.Thread(target=read)
    reader.start()
    writers = [threading.Thread(target=write, args=(p,)) for p in policies]
    for w in writers:
        w.start()
    for w in writers:
        w.join()
    stop.set()
    reader.join()
    assert bad == []
    assert store.get_policy() in policies
    assert len(_journal(store)) == 120
