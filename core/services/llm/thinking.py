"""
Extended-thinking / reasoning-effort budgets.

Hard problems benefit from giving the model a private reasoning scratchpad;
simple, high-volume tasks do not — over-provisioning thinking wastes tokens
and can degrade output by making the model second-guess settled reasoning.

This module maps a coarse *effort level* (or an explicit token budget) onto a
provider thinking configuration. It is opt-in: callers pass ``effort=`` or
``thinking_budget=`` through to a provider; when neither is given, nothing is
applied and behaviour is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class EffortLevel(str, Enum):
    """Coarse reasoning-effort tiers matched to task cognitive load."""

    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Sweet-spot thinking budgets (tokens) per tier. Max effort is not always best
# effort, so HIGH is bounded well below arbitrary maxima.
_EFFORT_BUDGETS: dict[EffortLevel, int] = {
    EffortLevel.OFF: 0,
    EffortLevel.LOW: 3000,
    EffortLevel.MEDIUM: 6000,
    EffortLevel.HIGH: 12000,
}

# Minimum head-room between the thinking budget and ``max_tokens``: the visible
# answer needs room beyond the reasoning scratchpad.
_ANSWER_HEADROOM_TOKENS = 1024


@dataclass(frozen=True)
class ThinkingPlan:
    """Resolved thinking configuration for a single model call."""

    enabled: bool
    budget_tokens: int
    max_tokens: int

    def to_anthropic_kwargs(self) -> dict[str, Any]:
        """
        Render call kwargs for the Anthropic Messages API.

        When enabled, the API requires ``max_tokens > budget_tokens`` and a
        neutral temperature, so both are set here.
        """
        if not self.enabled:
            return {"max_tokens": self.max_tokens}
        return {
            "max_tokens": self.max_tokens,
            "temperature": 1.0,
            "thinking": {
                "type": "enabled",
                "budget_tokens": self.budget_tokens,
            },
        }


def budget_for_effort(level: EffortLevel) -> int:
    """Return the thinking token budget for a tier (0 when off)."""
    return _EFFORT_BUDGETS[level]


def _coerce_effort(value: object) -> EffortLevel | None:
    """Best-effort conversion of a user-supplied effort value to a tier."""
    if value is None:
        return None
    if isinstance(value, EffortLevel):
        return value
    try:
        return EffortLevel(str(value).strip().lower())
    except ValueError:
        return None


def resolve_thinking(
    *,
    effort: object = None,
    thinking_budget: int | None = None,
    max_tokens: int = 4096,
) -> ThinkingPlan:
    """
    Resolve an effort level / explicit budget into a :class:`ThinkingPlan`.

    Args:
        effort: An :class:`EffortLevel` or its string value (off/low/medium/high).
        thinking_budget: Explicit budget in tokens; overrides ``effort`` when > 0.
        max_tokens: The caller's requested visible-output budget.

    Returns:
        ThinkingPlan: ``enabled=False`` (no thinking) when no budget resolves,
        otherwise a plan whose ``max_tokens`` is grown to leave answer head-room
        above the thinking budget.
    """
    budget = 0
    if thinking_budget is not None and thinking_budget > 0:
        budget = thinking_budget
    else:
        level = _coerce_effort(effort)
        if level is not None:
            budget = budget_for_effort(level)

    if budget <= 0:
        return ThinkingPlan(enabled=False, budget_tokens=0, max_tokens=max_tokens)

    required = budget + _ANSWER_HEADROOM_TOKENS
    return ThinkingPlan(
        enabled=True,
        budget_tokens=budget,
        max_tokens=max(max_tokens, required),
    )
