"""
Generator-vs-Challenger debate protocol.

Implements the high-stakes adversarial pattern from book ch6: one agent
produces an answer, a second adversarial agent critiques it, and a judge
returns ``APPROVED`` / ``REVISE`` / ``REJECT``. The loop continues until a
terminal verdict is reached or ``max_rounds`` is exhausted.

This complements the multi-persona ``InternalDebate`` already in
``core.meta.debate``, which is consensus-oriented. Use Generator-Challenger
when factual accuracy matters more than consensus.

The protocol is LLM-agnostic: ``GeneratorFn``, ``ChallengerFn`` and
``JudgeFn`` are caller-supplied async callables, so the same primitive
works with any provider, mock harness, or test double.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Final, Sequence


class Verdict(str, Enum):
    """Judge verdict on a Generator answer."""

    APPROVED = "approved"
    REVISE = "revise"
    REJECT = "reject"


DEFAULT_MAX_ROUNDS: Final[int] = 3


@dataclass(frozen=True)
class Critique:
    """Output of a Challenger turn."""

    text: str
    verdict_hint: Verdict | None = None


@dataclass(frozen=True)
class DebateRound:
    """Immutable record of one round (generator + challenger + verdict)."""

    round_index: int
    generator_output: str
    critique: Critique
    verdict: Verdict


@dataclass(frozen=True)
class DebateOutcome:
    """Final result of a full debate."""

    final_answer: str
    final_verdict: Verdict
    rounds: list[DebateRound] = field(default_factory=list)
    history: list[str] = field(default_factory=list)

    @property
    def approved(self) -> bool:
        return self.final_verdict is Verdict.APPROVED

    @property
    def round_count(self) -> int:
        return len(self.rounds)


GeneratorFn = Callable[[str, Sequence[DebateRound]], Awaitable[str] | str]
ChallengerFn = Callable[[str, Sequence[DebateRound]], Awaitable[Critique] | Critique]
JudgeFn = Callable[[str, Critique], Awaitable[Verdict] | Verdict]


async def _await_or_call(result: Awaitable[object] | object) -> object:
    """Return the value of ``result`` whether it is a coroutine or a value."""
    if inspect.isawaitable(result):
        return await result
    return result


class GeneratorChallengerProtocol:
    """Bounded Generator-vs-Challenger debate driver."""

    def __init__(
        self,
        *,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        accept_verdicts: tuple[Verdict, ...] = (Verdict.APPROVED,),
        terminal_verdicts: tuple[Verdict, ...] = (
            Verdict.APPROVED,
            Verdict.REJECT,
        ),
    ) -> None:
        if max_rounds <= 0:
            raise ValueError("max_rounds must be > 0")
        if not accept_verdicts:
            raise ValueError("accept_verdicts must contain at least one verdict")
        self._max_rounds = max_rounds
        self._accept = set(accept_verdicts)
        self._terminal = set(terminal_verdicts) | self._accept

    async def run(
        self,
        prompt: str,
        *,
        generator: GeneratorFn,
        challenger: ChallengerFn,
        judge: JudgeFn,
    ) -> DebateOutcome:
        """Run the debate. Always returns a ``DebateOutcome``."""
        rounds: list[DebateRound] = []
        history: list[str] = [prompt]
        last_answer: str = ""
        last_verdict: Verdict = Verdict.REVISE

        for i in range(1, self._max_rounds + 1):
            answer = await _await_or_call(generator(prompt, tuple(rounds)))
            if not isinstance(answer, str):
                raise TypeError(
                    f"generator must return str, got {type(answer).__name__}"
                )
            last_answer = answer
            history.append(f"[round {i}] generator: {answer}")

            critique = await _await_or_call(challenger(answer, tuple(rounds)))
            if not isinstance(critique, Critique):
                raise TypeError(
                    f"challenger must return Critique, got {type(critique).__name__}"
                )
            history.append(f"[round {i}] challenger: {critique.text}")

            verdict = await _await_or_call(judge(answer, critique))
            if not isinstance(verdict, Verdict):
                raise TypeError(
                    f"judge must return Verdict, got {type(verdict).__name__}"
                )
            last_verdict = verdict

            rounds.append(
                DebateRound(
                    round_index=i,
                    generator_output=answer,
                    critique=critique,
                    verdict=verdict,
                )
            )

            if verdict in self._terminal:
                break

        return DebateOutcome(
            final_answer=last_answer,
            final_verdict=last_verdict,
            rounds=rounds,
            history=history,
        )
