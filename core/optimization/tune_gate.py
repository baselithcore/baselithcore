"""Eval gate for automated prompt tuning.

``PromptOptimizer.auto_tune`` can rewrite an agent's system prompt from
production feedback — self-modification that, ungated, ships whatever the
meta-prompt produced. This module holds the non-negotiable between
generation and application: the candidate must pass an evaluation before
``apply_fn`` runs, and an accepted candidate is landed as a versioned
``PromptVersion`` labelled ``candidate`` in the prompt registry, so the
change has a diff, a version and a rollback path instead of vanishing into
a mutable string.

Off by default this release: enable with ``BASELITH_OPTIMIZER_EVAL_GATE=true``.
When enabled the posture is strictly fail-closed — no evaluator, a raising
evaluator, or a below-threshold score all refuse the application.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: ``(agent_id, candidate_prompt) -> score in [0, 1]`` — typically an
#: adapter over ``core.evaluation.prompt_eval.PromptEvaluator`` or the
#: regression runner, replaying the agent's suite against the candidate.
TuneEvaluator = Callable[[str, str], Awaitable[float]]

DEFAULT_TUNE_THRESHOLD = 0.9

_ENV = "BASELITH_OPTIMIZER_EVAL_GATE"

#: Registry label carried by accepted-but-not-yet-promoted candidates.
CANDIDATE_LABEL = "candidate"


def eval_gate_enabled() -> bool:
    """Whether auto-tune applications must pass the eval gate (default off)."""
    return os.environ.get(_ENV, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class TuneGateDecision:
    """Outcome of gating one tuning candidate."""

    agent_id: str
    accepted: bool
    score: float
    reason: str = ""
    registered_version: str | None = None


async def review_candidate(
    agent_id: str,
    candidate_prompt: str,
    evaluator: TuneEvaluator | None,
    *,
    threshold: float = DEFAULT_TUNE_THRESHOLD,
    register_as: str | None = None,
) -> TuneGateDecision:
    """Gate a tuning candidate; register it on acceptance.

    Args:
        agent_id: The agent whose prompt is being tuned.
        candidate_prompt: The generated replacement prompt.
        evaluator: Eval-suite adapter; ``None`` rejects (fail closed).
        threshold: Minimum passing score.
        register_as: Registry prompt name to land the accepted candidate
            under (label ``candidate``); ``None`` skips registration.

    Returns:
        The decision, audited either way as a ``self_modify`` event.
    """
    if evaluator is None:
        decision = TuneGateDecision(
            agent_id, False, 0.0, reason="no tune evaluator configured"
        )
        await _audit(decision)
        return decision
    try:
        score = float(await evaluator(agent_id, candidate_prompt))
    except Exception as exc:
        decision = TuneGateDecision(
            agent_id, False, 0.0, reason=f"evaluator failed: {exc}"
        )
        await _audit(decision)
        return decision

    if score < threshold:
        decision = TuneGateDecision(
            agent_id,
            False,
            score,
            reason=f"score {score:.3f} below threshold {threshold:.3f}",
        )
        await _audit(decision)
        return decision

    registered: str | None = None
    if register_as is not None:
        registered = _register_candidate(register_as, candidate_prompt)
    decision = TuneGateDecision(agent_id, True, score, registered_version=registered)
    await _audit(decision)
    return decision


def _register_candidate(name: str, template: str) -> str | None:
    """Land the candidate as the next registry version, labelled candidate."""
    try:
        from core.prompts.registry import get_prompt_registry

        registry = get_prompt_registry()
        version = str(len(registry.list_versions(name)) + 1)
        registry.register(name, template, version=version, labels={CANDIDATE_LABEL})
        return version
    except Exception as exc:
        logger.warning("tune_candidate_registration_failed name=%s error=%s", name, exc)
        return None


async def _audit(decision: TuneGateDecision) -> None:
    """Record the gate decision on the audit trail (never raises)."""
    try:
        from core.observability.audit import AuditEventType, get_audit_logger

        event = (
            AuditEventType.SELF_MODIFY_APPLY
            if decision.accepted
            else AuditEventType.SELF_MODIFY_REJECT
        )
        await get_audit_logger().log(
            event,
            resource=decision.agent_id,
            action="prompt_tune.gate",
            details={
                "score": decision.score,
                "reason": decision.reason,
                "registered_version": decision.registered_version,
            },
            success=decision.accepted,
        )
    except Exception:  # pragma: no cover - observability only
        logger.debug("tune_gate_audit_failed", exc_info=True)


__all__ = [
    "CANDIDATE_LABEL",
    "DEFAULT_TUNE_THRESHOLD",
    "TuneEvaluator",
    "TuneGateDecision",
    "eval_gate_enabled",
    "review_candidate",
]
