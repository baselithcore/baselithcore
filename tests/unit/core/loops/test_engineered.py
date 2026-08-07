"""EngineeredLoop: the loop owns verification, futility and escalation."""

import pytest

from core.loops.engineered import AttemptContext, EngineeredLoop
from core.orchestration.limits import LoopBudget, LoopLimits


def _verifier(script: list[tuple[bool, str]]):
    """Return an async verifier that replays *script* one entry per call."""
    calls = iter(script)

    async def verify() -> tuple[bool, str]:
        return next(calls)

    return verify


class TestEngineeredLoop:
    async def test_success_on_first_attempt(self):
        seen: list[AttemptContext] = []

        async def act(ctx: AttemptContext) -> None:
            seen.append(ctx)

        loop = EngineeredLoop(act=act, verify=_verifier([(True, "214 passed")]))
        outcome = await loop.run("pytest green")

        assert outcome.succeeded is True
        assert outcome.attempts == 1
        assert seen[0].lessons == ""

    async def test_lessons_are_fed_forward_not_transcripts(self):
        seen: list[AttemptContext] = []

        async def act(ctx: AttemptContext) -> None:
            seen.append(ctx)

        loop = EngineeredLoop(
            act=act,
            verify=_verifier(
                [
                    (False, "FAILED test_a.py::x - AssertionError"),
                    (False, "FAILED test_b.py::y - TypeError"),
                    (True, "ok"),
                ]
            ),
            max_attempts=3,
        )
        outcome = await loop.run("pytest green")

        assert outcome.succeeded is True
        assert seen[1].lessons.startswith("Previous attempts failed")
        assert "Attempt 1 failed" in seen[2].lessons
        assert "Attempt 2 failed" in seen[2].lessons
        # The raw evidence of attempt 1 is not replayed into attempt 3.
        assert seen[2].last_evidence == "FAILED test_b.py::y - TypeError"

    async def test_repeated_failure_stalls_before_exhausting_attempts(self):
        attempts = 0

        async def act(_: AttemptContext) -> None:
            nonlocal attempts
            attempts += 1

        loop = EngineeredLoop(
            act=act,
            verify=_verifier([(False, "FAILED same - boom")] * 10),
            max_attempts=10,
            stall_threshold=2,
        )
        outcome = await loop.run("pytest green")

        assert outcome.status == "stalled"
        assert attempts == 2  # stopped early instead of burning eight more
        assert outcome.reason and "no progress" in outcome.reason

    async def test_distinct_failures_run_to_exhaustion(self):
        async def act(_: AttemptContext) -> None:
            return None

        loop = EngineeredLoop(
            act=act,
            verify=_verifier([(False, f"FAILED case{i} - boom") for i in range(3)]),
            max_attempts=3,
            stall_threshold=2,
        )
        outcome = await loop.run("pytest green")

        assert outcome.status == "exhausted"
        assert outcome.attempts == 3

    async def test_actor_exception_is_a_failed_attempt_not_a_crash(self):
        async def act(_: AttemptContext) -> None:
            raise RuntimeError("patch did not apply")

        loop = EngineeredLoop(
            act=act,
            verify=_verifier([]),  # never reached: the actor raised
            max_attempts=2,
            stall_threshold=None,
        )
        outcome = await loop.run("pytest green")

        assert outcome.status == "exhausted"
        assert "patch did not apply" in outcome.evidence

    async def test_escalation_receives_resumable_state(self):
        captured = {}

        async def act(_: AttemptContext) -> None:
            return None

        async def escalate(outcome) -> None:
            captured["state"] = outcome.to_state()

        loop = EngineeredLoop(
            act=act,
            verify=_verifier([(False, "FAILED x - boom")] * 2),
            max_attempts=2,
            stall_threshold=2,
            escalate=escalate,
        )
        outcome = await loop.run("all tests pass")

        assert outcome.status == "stalled"
        state = captured["state"]
        assert state["goal"] == "all tests pass"
        assert state["status"] == "stalled"
        assert state["lessons"]

    async def test_escalation_failure_does_not_mask_the_outcome(self):
        async def act(_: AttemptContext) -> None:
            return None

        async def escalate(_outcome) -> None:
            raise RuntimeError("pager is down")

        loop = EngineeredLoop(
            act=act,
            verify=_verifier([(False, "FAILED x")]),
            max_attempts=1,
            escalate=escalate,
        )
        outcome = await loop.run("goal")
        assert outcome.status == "exhausted"

    async def test_budget_exhaustion_ends_the_loop_without_raising(self):
        async def act(_: AttemptContext) -> None:
            return None

        budget = LoopBudget(LoopLimits(max_iterations=1))
        loop = EngineeredLoop(
            act=act,
            verify=_verifier([(False, "FAILED x - boom")]),
            max_attempts=5,
            budget=budget,
        )
        outcome = await loop.run("goal")

        assert outcome.status == "budget_exceeded"
        assert outcome.attempts == 1

    async def test_max_attempts_must_be_positive(self):
        async def act(_: AttemptContext) -> None:
            return None

        with pytest.raises(ValueError):
            EngineeredLoop(act=act, verify=_verifier([]), max_attempts=0)
