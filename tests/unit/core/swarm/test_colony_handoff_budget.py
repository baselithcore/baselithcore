"""Tests for multi-agent handoff + budget/cancellation propagation."""

import asyncio

import pytest

from core.config.swarm import AuctionConfig, SwarmConfig
from core.orchestration.limits import BudgetExceededError, LoopBudgetSnapshot
from core.swarm.colony import Colony
from core.swarm.types import AgentProfile, Capability, MessageType, Task

pytestmark = [pytest.mark.contract]


def _colony():
    # Deterministic auction. a1 and a2 have identical profiles, so their bids
    # tie; the default "random" tie-breaker made this suite flaky — the task was
    # sometimes assigned to a2, leaving no free helper for a1's handoff (a2 busy,
    # a1 excluded as the requester) → handoff returned None. "first" makes the
    # first bidder (a1) win, so a1 holds the task and a2 stays free to receive it.
    colony = Colony(config=SwarmConfig(auction=AuctionConfig(tie_breaker="first")))
    for aid in ("a1", "a2"):
        colony.register_agent(
            AgentProfile(
                id=aid,
                name=aid,
                capabilities=[Capability(name="cap1", proficiency=0.9)],
                success_rate=0.9,
            )
        )
    return colony


@pytest.mark.asyncio
class TestHandoff:
    async def test_handoff_reassigns_and_emits_message(self):
        colony = _colony()
        task = Task(description="t", required_capabilities=["cap1"])
        await colony.submit_task(task)

        received = []
        colony.on_message(MessageType.HANDOFF, received.append)

        ho = await colony.handoff(
            "a1", task.id, reason="needs cap", context={"partial": 42}
        )
        assert ho is not None
        assert ho.from_agent == "a1"
        assert ho.to_agent != "a1"
        assert ho.context == {"partial": 42}
        assert colony._tasks[task.id].assigned_to == ho.to_agent
        # A directed HANDOFF message was broadcast carrying the context.
        assert len(received) == 1
        assert received[0].type == MessageType.HANDOFF
        assert received[0].payload["reason"] == "needs cap"
        assert received[0].payload["context"] == {"partial": 42}

    async def test_handoff_explicit_recipient(self):
        colony = _colony()
        task = Task(description="t", required_capabilities=["cap1"])
        await colony.submit_task(task)
        ho = await colony.handoff("a1", task.id, to_agent="a2")
        assert ho is not None and ho.to_agent == "a2"

    async def test_handoff_unknown_task_returns_none(self):
        colony = _colony()
        assert await colony.handoff("a1", "nope") is None

    async def test_to_message_roundtrip(self):
        from core.swarm.types import Handoff

        ho = Handoff(task_id="t1", from_agent="a", to_agent="b", reason="r")
        msg = ho.to_message()
        assert msg.type == MessageType.HANDOFF
        assert msg.sender_id == "a" and msg.receiver_id == "b"
        assert msg.payload["task_id"] == "t1"


@pytest.mark.asyncio
class TestBatchBudgetCancellation:
    async def test_budget_breach_aborts_batch(self):
        """A shared-budget breach in one sub-agent cancels siblings + re-raises."""
        colony = _colony()
        started = []
        cancelled = []

        async def execute_fn(task, agent):
            if task.description == "boom":
                raise BudgetExceededError(
                    "max_tokens", LoopBudgetSnapshot(1, 1, 0.0, 999)
                )
            started.append(task.id)
            try:
                await asyncio.sleep(5)  # long — should be cancelled by the group
            except asyncio.CancelledError:
                cancelled.append(task.id)
                raise
            return "done"

        # execute_batch performs its own auction allocation — don't pre-submit
        # (that would leave the agents busy and starve the internal auction).
        tasks = [
            Task(description="boom", required_capabilities=["cap1"]),
            Task(description="slow1", required_capabilities=["cap1"]),
        ]

        with pytest.raises(BudgetExceededError) as exc:
            await colony.execute_batch(tasks, execute_fn)
        assert exc.value.reason == "max_tokens"
        # Any sibling that had started was cancelled (structured concurrency).
        assert set(cancelled).issubset(set(started))

    async def test_ordinary_failure_does_not_abort_batch(self):
        """A normal per-task exception is recorded, not fatal to the batch."""
        colony = _colony()

        async def execute_fn(task, agent):
            if task.description == "fail":
                raise ValueError("boom")
            return f"ok:{task.id}"

        tasks = [
            Task(description="fail", required_capabilities=["cap1"]),
            Task(description="good", required_capabilities=["cap1"]),
        ]
        result = await colony.execute_batch(tasks, execute_fn)
        # Both tasks accounted for; the batch completed despite one failure.
        assert len(result.completed) + len(result.failed) == 2
        assert any("boom" in v for v in result.failed.values())
