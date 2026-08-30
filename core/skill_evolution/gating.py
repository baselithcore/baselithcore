"""Validation gate for evolved skills.

The loop's key invariant lives here: a skill version that does not beat
the previous best validation score is rolled back, but the wiki (pattern
store) is never touched — knowledge persists even when the skill built
from it regresses. The validator is an injected async callable
(``skill_name -> score``); production callers can adapt
``core/evaluation/regression_runner.py``.

Failure posture is strictly fail-closed: a raising validator REJECTS the
version under review (rolled back, best score untouched) — including the
very first version of a skill, which would otherwise slip through
unvalidated.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from core.observability.logging import get_logger
from core.skill_evolution.types import FitnessVector, GateDecision
from core.skill_evolution.writer import ManagedSkillWriter

logger = get_logger(__name__)

__all__ = ["SkillGate"]

#: A validator returns either a scalar score or a multi-objective
#: :class:`FitnessVector` (scalarized for the gate comparison).
ValidateFn = Callable[[str], Awaitable[float | FitnessVector]]


async def _audit_gate(
    event: str, skill_name: str, details: dict[str, object], *, success: bool
) -> None:
    """Record the gate decision on the audit trail (never raises)."""
    try:
        from core.observability.audit import AuditEventType, get_audit_logger

        await get_audit_logger().log(
            AuditEventType(event),
            resource=skill_name,
            action="skill_evolution.gate",
            details=details,
            success=success,
        )
    except Exception:  # pragma: no cover - observability must not break gates
        logger.debug("skill_gate_audit_failed", exc_info=True)


class SkillGate:
    """Accept or roll back the current version of a managed skill."""

    def __init__(self, writer: ManagedSkillWriter) -> None:
        self._writer = writer

    async def review(
        self,
        skill_name: str,
        validate: ValidateFn,
    ) -> GateDecision:
        """Gate the currently written version of ``skill_name``.

        Accept iff the skill exists, the validator succeeds, and its score
        strictly beats the recorded best (or no best exists yet). Any other
        outcome — unknown skill, raising validator, non-improving score —
        rejects and rolls the skill back to its previous version.

        Args:
            skill_name: Managed skill to review.
            validate: Async validator returning a score in ``[0, 1]``.

        Returns:
            The :class:`GateDecision` taken.
        """
        meta = await self._writer.read_meta(skill_name)
        best = meta["best_score"]

        if meta["version"] < 1:
            logger.warning("Gate review of nonexistent skill '%s'", skill_name)
            return GateDecision(
                skill_name=skill_name, accepted=False, score=0.0, previous_best=best
            )

        fitness: FitnessVector | None = None
        try:
            raw = await validate(skill_name)
            if isinstance(raw, FitnessVector):
                fitness = raw
                score = raw.scalarize()
            else:
                score = float(raw)
        except Exception as exc:
            logger.warning(
                "Validator failed for skill '%s', rejecting (fail closed): %s",
                skill_name,
                exc,
            )
            rolled_back = await self._writer.rollback(skill_name)
            await _audit_gate(
                "self_modify.reject",
                skill_name,
                {
                    "reason": f"validator failed: {exc}",
                    "previous_best": best,
                    "rolled_back": rolled_back,
                    "version": meta["version"],
                },
                success=False,
            )
            return GateDecision(
                skill_name=skill_name,
                accepted=False,
                score=0.0,
                previous_best=best,
                rolled_back=rolled_back,
            )

        details: dict[str, object] = {
            "score": score,
            "previous_best": best,
            "version": meta["version"],
        }
        if fitness is not None:
            details["fitness"] = {
                "quality": fitness.quality,
                "latency_s": fitness.latency_s,
                "cost_usd": fitness.cost_usd,
            }

        if best is None or score > best:
            await self._writer.update_best_score(skill_name, score)
            await _audit_gate("self_modify.apply", skill_name, details, success=True)
            return GateDecision(
                skill_name=skill_name,
                accepted=True,
                score=score,
                previous_best=best,
            )

        rolled_back = await self._writer.rollback(skill_name)
        logger.info(
            "Skill '%s' rejected (score %.3f <= best %.3f), rolled back: %s",
            skill_name,
            score,
            best,
            rolled_back,
        )
        await _audit_gate(
            "self_modify.reject",
            skill_name,
            {**details, "rolled_back": rolled_back},
            success=False,
        )
        return GateDecision(
            skill_name=skill_name,
            accepted=False,
            score=score,
            previous_best=best,
            rolled_back=rolled_back,
        )
