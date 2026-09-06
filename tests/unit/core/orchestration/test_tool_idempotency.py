"""Exactly-once *effects* for tool calls, given at-least-once delivery.

The queue redelivers, the loop resumes from a checkpoint, an operator replays a
dead-lettered job. Each of those can run the same tool call twice — and for a
payment, an outbound webhook or an email, twice is a defect the user sees.

The ledger records intent before the call and the outcome after it, keyed by a
value derived from the call itself. A replay of the same call finds the
recorded outcome and returns it instead of executing again.
"""

from __future__ import annotations

import pytest

from core.orchestration.idempotency import (
    InMemoryToolLedger,
    ToolOutcome,
    derive_idempotency_key,
    requires_idempotency,
)


class TestKeyDerivation:
    def test_the_same_call_yields_the_same_key(self) -> None:
        first = derive_idempotency_key("run-1", 3, "charge", {"amount": 10, "to": "x"})
        second = derive_idempotency_key("run-1", 3, "charge", {"amount": 10, "to": "x"})

        assert first == second

    def test_argument_order_does_not_change_the_key(self) -> None:
        """A dict is unordered; two spellings of one call must not double-charge."""
        first = derive_idempotency_key("run-1", 3, "charge", {"amount": 10, "to": "x"})
        second = derive_idempotency_key("run-1", 3, "charge", {"to": "x", "amount": 10})

        assert first == second

    @pytest.mark.parametrize(
        ("run_id", "step", "tool", "args"),
        [
            ("run-2", 3, "charge", {"amount": 10}),
            ("run-1", 4, "charge", {"amount": 10}),
            ("run-1", 3, "refund", {"amount": 10}),
            ("run-1", 3, "charge", {"amount": 11}),
            ("run-1", 3, "charge", {"amount": "10"}),
        ],
    )
    def test_any_difference_yields_a_different_key(
        self, run_id: str, step: int, tool: str, args: dict[str, object]
    ) -> None:
        baseline = derive_idempotency_key("run-1", 3, "charge", {"amount": 10})

        assert derive_idempotency_key(run_id, step, tool, args) != baseline

    def test_unserialisable_arguments_do_not_break_the_call(self) -> None:
        """A key is still derived; it just cannot dedupe across processes."""
        key = derive_idempotency_key("run-1", 1, "t", {"fn": object()})

        assert isinstance(key, str) and len(key) == 64

    def test_the_key_is_a_hex_digest_not_the_arguments(self) -> None:
        """Keys land in logs and a database column: no payload may leak."""
        key = derive_idempotency_key("run-1", 1, "charge", {"iban": "IT60X0542811101"})

        assert "IT60X" not in key
        assert len(key) == 64 and all(c in "0123456789abcdef" for c in key)


class TestRequiresIdempotency:
    @pytest.mark.parametrize(
        ("category", "expected"),
        [
            ("read_only", False),
            ("mutating", True),
            ("destructive", True),
            ("external_side_effect", True),
            ("", True),
            ("nonsense", True),
        ],
    )
    def test_only_read_only_is_exempt(self, category: str, expected: bool) -> None:
        """Unknown categories are treated as effectful — fail closed."""
        assert requires_idempotency(category) is expected


class TestInMemoryLedger:
    @pytest.mark.asyncio
    async def test_a_first_call_is_not_a_replay(self) -> None:
        ledger = InMemoryToolLedger()

        assert await ledger.lookup("k1") is None

    @pytest.mark.asyncio
    async def test_a_recorded_outcome_is_returned_on_replay(self) -> None:
        ledger = InMemoryToolLedger()
        await ledger.begin("k1", run_id="run-1", tool="charge")
        await ledger.complete("k1", "receipt-42")

        replay = await ledger.lookup("k1")

        assert replay is not None
        assert replay.result == "receipt-42"
        assert replay.status == "completed"

    @pytest.mark.asyncio
    async def test_an_in_flight_call_is_reported_as_such(self) -> None:
        """A crash between begin and complete must not look like "never ran"."""
        ledger = InMemoryToolLedger()
        await ledger.begin("k1", run_id="run-1", tool="charge")

        pending = await ledger.lookup("k1")

        assert pending is not None and pending.status == "in_flight"
        assert pending.result is None

    @pytest.mark.asyncio
    async def test_a_failed_call_may_be_retried(self) -> None:
        ledger = InMemoryToolLedger()
        await ledger.begin("k1", run_id="run-1", tool="charge")
        await ledger.fail("k1", "gateway timeout")

        recorded = await ledger.lookup("k1")

        assert recorded is not None and recorded.status == "failed"
        assert recorded.is_replayable is False

    @pytest.mark.asyncio
    async def test_only_a_completed_outcome_is_replayable(self) -> None:
        ledger = InMemoryToolLedger()
        await ledger.begin("k1", run_id="run-1", tool="charge")
        assert (await ledger.lookup("k1")).is_replayable is False  # type: ignore[union-attr]

        await ledger.complete("k1", "ok")
        assert (await ledger.lookup("k1")).is_replayable is True  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_distinct_keys_do_not_collide(self) -> None:
        ledger = InMemoryToolLedger()
        await ledger.begin("k1", run_id="r", tool="t")
        await ledger.complete("k1", "one")
        await ledger.begin("k2", run_id="r", tool="t")
        await ledger.complete("k2", "two")

        assert (await ledger.lookup("k1")).result == "one"  # type: ignore[union-attr]
        assert (await ledger.lookup("k2")).result == "two"  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_the_ledger_is_bounded(self) -> None:
        """An unbounded dict in a long-lived worker is a memory leak."""
        ledger = InMemoryToolLedger(maxsize=3)
        for index in range(5):
            await ledger.begin(f"k{index}", run_id="r", tool="t")
            await ledger.complete(f"k{index}", index)

        assert await ledger.lookup("k0") is None
        assert (await ledger.lookup("k4")).result == 4  # type: ignore[union-attr]


class TestOutcome:
    def test_outcome_reports_replayability_from_status(self) -> None:
        assert ToolOutcome(status="completed", result="x").is_replayable is True
        assert ToolOutcome(status="in_flight", result=None).is_replayable is False
        assert ToolOutcome(status="failed", result=None).is_replayable is False
