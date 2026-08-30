"""Orchestrator bridge for engineered loops.

Mirrors :class:`~core.workflows.flow_handler.WorkflowFlowHandler`: an
:class:`~core.loops.engineered.EngineeredLoop` becomes a flow handler that
can be registered for an intent like any other handler::

    orchestrator.register_handler(
        "fix_campaign", LoopFlowHandler(act_factory, verify_factory)
    )

The bridge wires the loop into the production machinery the bare primitive
lacks a path to:

* the request's :class:`~core.orchestration.limits.LoopBudget`
  (``context["loop_budget"]``) bounds the attempts;
* the terminal :class:`~core.loops.engineered.LoopOutcome` is persisted into
  the durable checkpoint (``plugin_data["loop_outcome"]``) so an escalated
  campaign is a resumable record, not a lost log line;
* every attempt writes a ``loop_last_progress_at`` heartbeat to the
  checkpoint, making a wedged campaign distinguishable from a slow one;
* attempt progress is published on the run-event stream;
* a non-success outcome escalates through
  :func:`~core.loops.escalation.build_default_escalation` unless the caller
  supplies a hook; and
* an optional wiki :class:`~core.skill_evolution.store.PatternStore` primes
  the first attempt with :func:`~core.loops.priming.prime_lessons` — later
  attempts still learn from the loop's own
  :class:`~core.loops.lessons.LessonLog`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from core.loops.engineered import (
    Actor,
    AttemptContext,
    EngineeredLoop,
    EscalationHook,
    Verifier,
)
from core.loops.escalation import build_default_escalation
from core.loops.goal import HardenedGoal
from core.loops.priming import prime_lessons
from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.skill_evolution.store import PatternStore

logger = get_logger(__name__)

ActFactory = Callable[[str, dict[str, Any]], Actor]
VerifyFactory = Callable[[str, dict[str, Any]], Verifier]


class LoopFlowHandler:
    """FlowHandler that runs an engineered loop for its registered intent."""

    def __init__(
        self,
        act_factory: ActFactory,
        verify_factory: VerifyFactory,
        *,
        max_attempts: int = 6,
        stall_threshold: int | None = 3,
        max_lessons: int = 10,
        escalate: EscalationHook | None = None,
        pattern_store: PatternStore | None = None,
    ) -> None:
        """
        Args:
            act_factory: Builds the per-request actor from (goal, context).
            verify_factory: Builds the per-request verifier from
                (goal, context) — the machine-checkable termination oracle.
            max_attempts: Hard attempt cap per request.
            stall_threshold: Consecutive identical failure fingerprints that
                count as futility (None disables).
            max_lessons: Feed-forward lesson cap.
            escalate: Hook for non-success outcomes; defaults to the
                composed human-notification + webhook sink built from the
                request context (``human_intervention`` / ``webhook_service``).
            pattern_store: Optional wiki pattern store. When set, the goal
                handed to the loop is primed with
                :func:`~core.loops.priming.prime_lessons` — goal-relevant
                past patterns — so the FIRST attempt starts informed;
                subsequent attempts additionally learn from the loop's own
                :class:`~core.loops.lessons.LessonLog` as usual. Priming is
                fail-soft: a store error logs and runs the goal unprimed.
                Default ``None`` leaves behavior byte-identical.
        """
        self._act_factory = act_factory
        self._verify_factory = verify_factory
        self._max_attempts = max_attempts
        self._stall_threshold = stall_threshold
        self._max_lessons = max_lessons
        self._escalate = escalate
        self._pattern_store = pattern_store

    async def handle(
        self, query: str | HardenedGoal, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Run the loop for ``query`` (a raw goal or a hardened one).

        When a ``pattern_store`` was supplied, the goal text handed to
        :meth:`EngineeredLoop.run` is prefixed with the
        :func:`~core.loops.priming.prime_lessons` block, so the first
        attempt already sees relevant past lessons (later attempts add the
        loop's own :class:`~core.loops.lessons.LessonLog` on top). The act
        and verify factories always receive the raw goal.

        Returns the orchestrator result shape: ``response`` carries the
        verifier evidence on success or a failure summary otherwise, with
        the full outcome under ``metadata["loop"]``.
        """
        goal = query.render() if isinstance(query, HardenedGoal) else query
        run_goal = await self._primed_goal(goal)
        escalate = self._escalate
        if escalate is None:
            escalate = build_default_escalation(
                human=context.get("human_intervention"),
                webhooks=context.get("webhook_service"),
                tenant_id=context.get("tenant_id") or "default",
            )

        checkpoint = context.get("checkpoint")
        run_id = context.get("run_id")
        act = self._act_factory(goal, context)

        async def observed_act(attempt_context: AttemptContext) -> Any:
            await self._heartbeat(checkpoint, attempt_context.attempt, run_id)
            return await act(attempt_context)

        loop = EngineeredLoop(
            act=observed_act,
            verify=self._verify_factory(goal, context),
            max_attempts=self._max_attempts,
            stall_threshold=self._stall_threshold,
            max_lessons=self._max_lessons,
            escalate=escalate,
            budget=context.get("loop_budget"),
        )
        outcome = await loop.run(run_goal)

        await self._persist_outcome(checkpoint, outcome)
        metadata = {"loop": outcome.to_state()}
        if outcome.succeeded:
            return {"response": outcome.evidence, "metadata": metadata}
        logger.warning(
            "loop_flow_handler_lost status=%s attempts=%d",
            outcome.status,
            outcome.attempts,
        )
        return {
            "response": (
                f"Loop {outcome.status} after {outcome.attempts} attempt(s): "
                f"{outcome.reason or outcome.evidence}"
            ),
            "error": True,
            "metadata": metadata,
        }

    async def _primed_goal(self, goal: str) -> str:
        """Prefix ``goal`` with past-campaign lessons when a store is set.

        Fail-soft: priming failures are logged and the raw goal runs — a
        broken pattern store must never take down the campaign itself.
        """
        if self._pattern_store is None:
            return goal
        try:
            primer = await prime_lessons(goal, self._pattern_store)
        except Exception as exc:
            logger.warning("loop_priming_failed error=%s", exc)
            return goal
        if not primer:
            return goal
        return f"{primer}\n\n{goal}"

    async def _heartbeat(
        self, checkpoint: Any | None, attempt: int, run_id: str | None
    ) -> None:
        """Record per-attempt progress durably and on the event stream."""
        if checkpoint is not None:
            try:
                checkpoint.checkpoint.plugin_data["loop_last_progress_at"] = time.time()
                await checkpoint.store.save(checkpoint.checkpoint)
            except Exception as exc:  # heartbeat must never fail the attempt
                logger.debug("loop_heartbeat_write_failed error=%s", exc)
        try:
            from core.api.events import EventType
            from core.orchestration.run_events import publish_run_event

            publish_run_event(
                run_id,
                EventType.THOUGHT,
                data={"loop_attempt": attempt},
                content=f"loop attempt {attempt}",
            )
        except Exception as exc:  # pragma: no cover - observability only
            logger.debug("loop_progress_event_failed error=%s", exc)

    async def _persist_outcome(self, checkpoint: Any | None, outcome: Any) -> None:
        if checkpoint is None:
            return
        try:
            checkpoint.checkpoint.plugin_data["loop_outcome"] = outcome.to_state()
            await checkpoint.store.save(checkpoint.checkpoint)
        except Exception as exc:
            logger.warning("loop_outcome_persist_failed error=%s", exc)


__all__ = ["ActFactory", "LoopFlowHandler", "VerifyFactory"]
