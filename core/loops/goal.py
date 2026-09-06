"""Pre-flight goal hardening for engineered loops.

An autonomous loop pays for every ambiguity in its goal: unclear scope means
wandering edits, an unstated verifier means the loop cannot end, no budget
means it ends at the money instead of the goal. :func:`harden_goal` runs the
interrogation *before* the loop spends anything — one cheap LLM call that
turns a loose request into a :class:`HardenedGoal` with explicit scope,
machine-checkable termination, budget and rollback plan.

Fail-soft by design: when the questionnaire cannot run or returns garbage,
the original goal is used unhardened — hardening is an upgrade, never a
gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from core.observability.logging import get_logger
from core.prompts.catalog import resolve_catalog_prompt

logger = get_logger(__name__)

_PROMPT_NAME = "loop_goal_hardening"

_FALLBACK_TEMPLATE = """You are hardening a goal before an autonomous loop runs it.

Interrogate the goal below and answer with your best inference (do not ask questions back):

GOAL: {{ goal }}

Return JSON with exactly these keys:
{
    "goal": "<the goal restated as a machine-checkable termination condition>",
    "scope": "<what the loop may and may not touch>",
    "verifier_description": "<the concrete check that decides done, e.g. a command and its expected result>",
    "budget": "<attempt/cost/time bounds appropriate to the task>",
    "rollback_plan": "<how to undo the work if the loop loses>"
}"""


@dataclass(frozen=True)
class HardenedGoal:
    """A goal interrogated into loop-ready form."""

    goal: str
    scope: str = ""
    verifier_description: str = ""
    budget: str = ""
    rollback_plan: str = ""

    def render(self) -> str:
        """Render as the goal text handed to ``EngineeredLoop.run``."""
        parts = [self.goal]
        for title, value in (
            ("Scope", self.scope),
            ("Verified by", self.verifier_description),
            ("Budget", self.budget),
            ("Rollback", self.rollback_plan),
        ):
            if value:
                parts.append(f"{title}: {value}")
        return "\n".join(parts)


async def harden_goal(goal: str, *, llm_service: Any | None = None) -> HardenedGoal:
    """Interrogate ``goal`` into a :class:`HardenedGoal` (fail-soft).

    Args:
        goal: The raw goal as the caller stated it.
        llm_service: LLM override; defaults to the shared service.

    Returns:
        The hardened goal, or ``HardenedGoal(goal=goal)`` when the
        questionnaire fails — the loop still runs, just unhardened.
    """
    prompt = resolve_catalog_prompt(
        _PROMPT_NAME,
        {"goal": goal},
        fallback_template=_FALLBACK_TEMPLATE,
    )
    try:
        if llm_service is None:
            from core.services.llm import get_llm_service

            llm_service = get_llm_service()
        raw = await llm_service.generate_response(prompt, json=True)
        data = json.loads(raw)
        return HardenedGoal(
            goal=str(data.get("goal") or goal),
            scope=str(data.get("scope") or ""),
            verifier_description=str(data.get("verifier_description") or ""),
            budget=str(data.get("budget") or ""),
            rollback_plan=str(data.get("rollback_plan") or ""),
        )
    except Exception as exc:
        logger.warning("goal_hardening_failed_using_raw_goal error=%s", exc)
        return HardenedGoal(goal=goal)


__all__ = ["HardenedGoal", "harden_goal"]
