"""Unit tests for ``core.meta.generator_challenger``."""

from __future__ import annotations

import pytest

from core.meta.generator_challenger import (
    Critique,
    DebateOutcome,
    GeneratorChallengerProtocol,
    Verdict,
)


def _make_generator(answers: list[str]):
    it = iter(answers)

    async def gen(prompt, history):
        return next(it)

    return gen


def _make_challenger(critiques: list[Critique]):
    it = iter(critiques)

    async def chal(answer, history):
        return next(it)

    return chal


def _make_judge(verdicts: list[Verdict]):
    it = iter(verdicts)

    async def judge(answer, critique):
        return next(it)

    return judge


class TestGeneratorChallengerProtocol:
    async def test_approved_first_round_terminates(self) -> None:
        proto = GeneratorChallengerProtocol(max_rounds=3)
        out = await proto.run(
            "draft an answer",
            generator=_make_generator(["v1"]),
            challenger=_make_challenger([Critique(text="ok")]),
            judge=_make_judge([Verdict.APPROVED]),
        )
        assert out.approved
        assert out.round_count == 1
        assert out.final_answer == "v1"

    async def test_revise_loops_until_approved(self) -> None:
        proto = GeneratorChallengerProtocol(max_rounds=4)
        out = await proto.run(
            "p",
            generator=_make_generator(["v1", "v2", "v3"]),
            challenger=_make_challenger(
                [Critique(text="c1"), Critique(text="c2"), Critique(text="c3")]
            ),
            judge=_make_judge([Verdict.REVISE, Verdict.REVISE, Verdict.APPROVED]),
        )
        assert out.approved
        assert out.round_count == 3
        assert out.final_answer == "v3"

    async def test_max_rounds_exhaustion_does_not_approve(self) -> None:
        proto = GeneratorChallengerProtocol(max_rounds=2)
        out = await proto.run(
            "p",
            generator=_make_generator(["a", "b"]),
            challenger=_make_challenger([Critique(text="c1"), Critique(text="c2")]),
            judge=_make_judge([Verdict.REVISE, Verdict.REVISE]),
        )
        assert not out.approved
        assert out.final_verdict is Verdict.REVISE
        assert out.round_count == 2

    async def test_reject_terminates_immediately(self) -> None:
        proto = GeneratorChallengerProtocol(max_rounds=5)
        out = await proto.run(
            "p",
            generator=_make_generator(["bad"]),
            challenger=_make_challenger([Critique(text="hostile")]),
            judge=_make_judge([Verdict.REJECT]),
        )
        assert out.final_verdict is Verdict.REJECT
        assert out.round_count == 1

    async def test_history_records_each_round(self) -> None:
        proto = GeneratorChallengerProtocol(max_rounds=3)
        out = await proto.run(
            "p",
            generator=_make_generator(["v1", "v2"]),
            challenger=_make_challenger([Critique(text="c1"), Critique(text="c2")]),
            judge=_make_judge([Verdict.REVISE, Verdict.APPROVED]),
        )
        assert out.history[0] == "p"
        assert any("generator: v1" in h for h in out.history)
        assert any("challenger: c2" in h for h in out.history)

    async def test_sync_callbacks_supported(self) -> None:
        def gen(prompt, history):
            return "sync answer"

        def chal(answer, history):
            return Critique(text="sync critique")

        def judge(answer, critique):
            return Verdict.APPROVED

        proto = GeneratorChallengerProtocol()
        out = await proto.run("p", generator=gen, challenger=chal, judge=judge)
        assert out.approved
        assert out.final_answer == "sync answer"

    def test_invalid_max_rounds_rejected(self) -> None:
        with pytest.raises(ValueError):
            GeneratorChallengerProtocol(max_rounds=0)

    def test_empty_accept_verdicts_rejected(self) -> None:
        with pytest.raises(ValueError):
            GeneratorChallengerProtocol(accept_verdicts=())

    async def test_generator_type_error_when_not_string(self) -> None:
        async def bad_gen(prompt, history):
            return 42  # not a str

        async def chal(answer, history):
            return Critique(text="x")

        async def judge(answer, critique):
            return Verdict.APPROVED

        proto = GeneratorChallengerProtocol()
        with pytest.raises(TypeError):
            await proto.run(
                "p",
                generator=bad_gen,  # type: ignore[arg-type]
                challenger=chal,
                judge=judge,
            )

    async def test_outcome_is_dataclass(self) -> None:
        proto = GeneratorChallengerProtocol(max_rounds=1)
        out = await proto.run(
            "p",
            generator=_make_generator(["x"]),
            challenger=_make_challenger([Critique(text="c")]),
            judge=_make_judge([Verdict.APPROVED]),
        )
        assert isinstance(out, DebateOutcome)
        assert isinstance(out.rounds[0].critique, Critique)
