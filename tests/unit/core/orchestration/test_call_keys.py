"""Content-addressed, path-independent call keys.

The property under test throughout: a resumed run that requests an effect it
already completed — in any order, at any position — lands on the key the
earlier pass recorded, while a deliberate second identical call in the same
run gets a key of its own.
"""

from __future__ import annotations

import pytest

from core.orchestration.call_keys import (
    CallOccurrences,
    call_step_key,
    canonical_arguments,
    derive_call_key,
)
from core.orchestration.checkpoint import Checkpoint, CheckpointManager, step_key
from core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from core.orchestration.idempotency import (
    InMemoryToolLedger,
    ToolOutcome,
    claim_call,
    derive_idempotency_key,
)


class TestDeriveCallKey:
    def test_argument_order_does_not_matter(self) -> None:
        assert derive_call_key("r", "charge", {"a": 1, "b": 2}, 0) == derive_call_key(
            "r", "charge", {"b": 2, "a": 1}, 0
        )

    def test_occurrence_tenant_run_tool_and_args_all_separate_keys(self) -> None:
        base = derive_call_key("r", "charge", {"a": 1}, 0, tenant_id="t1")
        variants = [
            derive_call_key("r", "charge", {"a": 1}, 1, tenant_id="t1"),
            derive_call_key("r", "charge", {"a": 1}, 0, tenant_id="t2"),
            derive_call_key("r2", "charge", {"a": 1}, 0, tenant_id="t1"),
            derive_call_key("r", "refund", {"a": 1}, 0, tenant_id="t1"),
            derive_call_key("r", "charge", {"a": 2}, 0, tenant_id="t1"),
        ]
        assert base not in variants
        assert len(set(variants)) == len(variants)

    def test_never_equals_the_legacy_key(self) -> None:
        """Domain-separated: a v2 key cannot collide with a positional one."""
        assert derive_call_key("r", "t", {}, 0) != derive_idempotency_key(
            "r", 0, "t", {}
        )

    def test_key_is_a_digest_not_a_payload(self) -> None:
        key = derive_call_key("r", "charge", {"iban": "IT60X0542811101"}, 0)
        assert len(key) == 64 and "IT60" not in key

    def test_unserialisable_arguments_still_derive(self) -> None:
        marker = object()
        assert canonical_arguments({"fn": marker}) == canonical_arguments(
            {"fn": marker}
        )


class TestCallOccurrences:
    def test_counts_identical_calls_only(self) -> None:
        seen = CallOccurrences()
        assert seen.next("charge", {"a": 1}) == 0
        assert seen.next("charge", {"a": 2}) == 0
        assert seen.next("charge", {"a": 1}) == 1
        assert seen.next("notify", {"a": 1}) == 0

    def test_a_new_pass_counts_from_zero(self) -> None:
        first, resumed = CallOccurrences(), CallOccurrences()
        first.next("charge", {"a": 1})
        assert resumed.next("charge", {"a": 1}) == 0

    def test_step_key_shape(self) -> None:
        key = call_step_key("charge", {"a": 1}, 2)
        assert key.startswith("v2:charge:") and key.endswith(":2")


class TestClaimCallLegacyRows:
    """Rows written under the positional scheme stay honoured."""

    async def test_a_completed_legacy_row_is_replayed_and_migrated(self) -> None:
        ledger = InMemoryToolLedger()
        legacy = derive_idempotency_key("r", 0, "charge", {"a": 1})
        await ledger.begin(legacy, run_id="r", tool="charge")
        await ledger.complete(legacy, "charged")
        key = derive_call_key("r", "charge", {"a": 1}, 0)

        held = await claim_call(
            ledger, key, run_id="r", tool="charge", legacy_key=legacy
        )

        assert held is not None and held.is_replayable and held.result == "charged"
        migrated = await ledger.lookup(key)
        assert migrated is not None and migrated.result == "charged"

    async def test_an_in_flight_legacy_row_holds_without_locking_the_new_key(
        self,
    ) -> None:
        ledger = InMemoryToolLedger()
        legacy = derive_idempotency_key("r", 0, "charge", {})
        await ledger.begin(legacy, run_id="r", tool="charge")
        key = derive_call_key("r", "charge", {}, 0)

        held = await claim_call(
            ledger, key, run_id="r", tool="charge", legacy_key=legacy
        )

        assert held is not None and held.status == "in_flight"
        # Released, so a retry after the legacy row resolves can claim it.
        released = await ledger.lookup(key)
        assert released is not None and released.status == "failed"

    async def test_no_legacy_row_means_the_caller_owns_the_call(self) -> None:
        ledger = InMemoryToolLedger()
        key = derive_call_key("r", "charge", {}, 0)
        legacy = derive_idempotency_key("r", 0, "charge", {})
        assert (
            await claim_call(ledger, key, run_id="r", tool="charge", legacy_key=legacy)
            is None
        )

    async def test_a_failing_legacy_read_does_not_block_the_call(self) -> None:
        class LookupBroken(InMemoryToolLedger):
            async def lookup(self, key: str) -> ToolOutcome | None:
                raise RuntimeError("db down")

        ledger = LookupBroken()
        key = derive_call_key("r", "charge", {}, 0)
        held = await claim_call(
            ledger, key, run_id="r", tool="charge", legacy_key="legacy"
        )
        assert held is None


async def _manager(store: InMemoryCheckpointStore, run_id: str) -> CheckpointManager:
    checkpoint = Checkpoint(run_id=run_id, query="q")
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


class TestCheckpointSteps:
    async def test_resume_on_a_different_path_replays_every_completed_step(
        self,
    ) -> None:
        store = InMemoryCheckpointStore()
        ran: list[str] = []

        def effect(name: str):
            async def _fn() -> str:
                ran.append(name)
                return f"done-{name}"

            return _fn

        first = await _manager(store, "run-path")
        await first.run_step("charge", {"a": 1}, effect("charge"))
        await first.run_step("notify", {"to": "x"}, effect("notify"))

        loaded = await store.load("run-path")
        assert loaded is not None
        resumed = CheckpointManager(store, loaded)
        # Different order, and an unrelated read in front of both.
        await resumed.run_step("lookup", {}, effect("lookup"))
        assert await resumed.run_step("notify", {"to": "x"}, effect("notify")) == (
            "done-notify"
        )
        assert await resumed.run_step("charge", {"a": 1}, effect("charge")) == (
            "done-charge"
        )

        assert ran == ["charge", "notify", "lookup"]

    async def test_a_genuine_second_identical_call_executes(self) -> None:
        store = InMemoryCheckpointStore()
        count = {"n": 0}

        async def notify() -> int:
            count["n"] += 1
            return count["n"]

        manager = await _manager(store, "run-twice")
        assert await manager.run_step("notify", {"to": "x"}, notify) == 1
        assert await manager.run_step("notify", {"to": "x"}, notify) == 2
        assert count["n"] == 2

    async def test_a_legacy_positional_checkpoint_still_replays(self) -> None:
        store = InMemoryCheckpointStore()
        checkpoint = Checkpoint(run_id="run-old", query="q")
        checkpoint.steps[step_key(0, "charge", {"a": 1})] = {
            "tool_name": "charge",
            "args": {"a": 1},
            "result": "charged-before-upgrade",
        }
        await store.save(checkpoint)
        manager = CheckpointManager(store, checkpoint)

        async def must_not_run() -> str:
            raise AssertionError("re-executed a step recorded under the old key")

        assert (
            await manager.run_step("charge", {"a": 1}, must_not_run)
            == "charged-before-upgrade"
        )

    async def test_explicit_occurrence_matches_the_drawn_one(self) -> None:
        store = InMemoryCheckpointStore()
        manager = await _manager(store, "run-occ")
        occurrence = manager.next_occurrence("t", {"a": 1})

        async def fn() -> str:
            return "ok"

        await manager.run_step("t", {"a": 1}, fn, occurrence=occurrence)
        assert call_step_key("t", {"a": 1}, 0) in manager.checkpoint.steps


@pytest.mark.parametrize("args", [None, {}])
def test_empty_arguments_are_one_identity(args: dict[str, int] | None) -> None:
    assert derive_call_key("r", "t", args, 0) == derive_call_key("r", "t", {}, 0)
