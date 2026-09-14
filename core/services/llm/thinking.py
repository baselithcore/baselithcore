"""
Extended-thinking / reasoning-effort budgets.

Hard problems benefit from giving the model a private reasoning scratchpad;
simple, high-volume tasks do not — over-provisioning thinking wastes tokens
and can degrade output by making the model second-guess settled reasoning.

This module maps a coarse *effort level* (or an explicit token budget) onto a
provider thinking configuration. It is opt-in: callers pass ``effort=`` or
``thinking_budget=`` through to a provider; when neither is given, nothing is
applied and behaviour is unchanged.

Which *shape* that configuration takes is now a per-family question, answered
by :mod:`core.services.llm.model_capabilities`: the newest Claude families
accept only ``thinking: {"type": "adaptive"}`` with the tier expressed as
``output_config.effort``, and reject ``budget_tokens`` — and ``temperature``
— with a 400. Older families still require the budget form. :meth:`
ThinkingPlan.to_kwargs` renders the right one for the target model;
:meth:`ThinkingPlan.to_anthropic_kwargs` is kept as the legacy, model-blind
spelling.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from core.services.llm.model_capabilities import (
    ThinkingMode,
    capabilities_for,
    clamp_effort,
    clamp_max_tokens,
)


class EffortLevel(str, Enum):
    """Coarse reasoning-effort tiers matched to task cognitive load.

    ``XHIGH`` and ``MAX`` mirror the tiers the API gained on the newest
    families; :func:`core.services.llm.model_capabilities.clamp_effort`
    degrades them where a model does not accept them.
    """

    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


# Sweet-spot thinking budgets (tokens) per tier, used for the legacy
# budget_tokens form. Max effort is not always best effort, so the tiers stay
# bounded well below arbitrary maxima.
_EFFORT_BUDGETS: dict[EffortLevel, int] = {
    EffortLevel.OFF: 0,
    EffortLevel.LOW: 3000,
    EffortLevel.MEDIUM: 6000,
    EffortLevel.HIGH: 12000,
    EffortLevel.XHIGH: 24000,
    EffortLevel.MAX: 32000,
}

# Minimum head-room between the thinking budget and ``max_tokens``: the visible
# answer needs room beyond the reasoning scratchpad.
_ANSWER_HEADROOM_TOKENS = 1024

# The API's floor for a ``budget_tokens`` request. Below it, thinking is
# dropped rather than requested at a size that would be rejected.
_MIN_THINKING_BUDGET_TOKENS = 1024


@dataclass(frozen=True)
class ThinkingPlan:
    """Resolved thinking configuration for a single model call.

    Attributes:
        enabled: Whether any thinking was requested at all.
        budget_tokens: Scratchpad budget for the legacy ``budget_tokens``
            form; meaningless on adaptive families, which size their own.
        max_tokens: Visible-output cap, grown to leave answer head-room above
            a legacy thinking budget.
        effort: The resolved tier, when the caller asked by tier rather than
            by raw budget.
    """

    enabled: bool
    budget_tokens: int
    max_tokens: int
    effort: EffortLevel | None = None

    def to_kwargs(self, model: str) -> dict[str, Any]:
        """Render call kwargs for *model*'s thinking surface.

        Args:
            model: Target model id; decides adaptive vs budget form and which
                effort tiers survive.

        Returns:
            dict: ``max_tokens`` plus, when thinking is on, either
            ``thinking: {"type": "adaptive"}`` (+ ``output_config.effort``) or
            the legacy ``{"type": "enabled", "budget_tokens": N}`` with the
            neutral ``temperature`` that form requires. Never a temperature on
            a family that rejects sampling parameters.
        """
        # ``resolve_thinking`` grows ``max_tokens`` to leave answer head-room
        # above the budget, with no idea which model it is for; a family with a
        # small ceiling would be asked for more than it can ever return.
        kwargs: dict[str, Any] = {
            "max_tokens": clamp_max_tokens(model, self.max_tokens)
        }
        if not self.enabled:
            return kwargs

        caps = capabilities_for(model)
        if caps.thinking_mode is ThinkingMode.NONE:
            return kwargs

        if caps.thinking_mode is ThinkingMode.ADAPTIVE:
            kwargs["thinking"] = {"type": "adaptive"}
            tier = clamp_effort(model, self.effort.value if self.effort else None)
            if tier is not None:
                kwargs["output_config"] = {"effort": tier}
            return kwargs

        # The API requires ``budget_tokens < max_tokens``; when the cap above
        # shrank, the budget has to shrink with it or the request is invalid.
        budget = min(self.budget_tokens, kwargs["max_tokens"] - _ANSWER_HEADROOM_TOKENS)
        if budget < _MIN_THINKING_BUDGET_TOKENS:
            # No budget fits under this family's ceiling: answer without a
            # scratchpad rather than send a request that is refused.
            return kwargs
        kwargs["thinking"] = {
            "type": "enabled",
            "budget_tokens": budget,
        }
        if caps.supports_sampling_params:
            # The budget form requires a neutral temperature; families that
            # reject sampling parameters never reach this branch in practice,
            # and must not be sent one if they do.
            kwargs["temperature"] = 1.0
        return kwargs

    def to_anthropic_kwargs(self) -> dict[str, Any]:
        """Render the legacy, model-blind budget form.

        Kept for callers written before the capability table existed. New code
        should call :meth:`to_kwargs` with the target model, which is the only
        way to avoid a 400 on the families that dropped ``budget_tokens``.
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


# Default effort tier per task category (values of
# ``core.models.routing.TaskCategory``, kept as strings to avoid importing the
# routing module here). Consulted by ``LLMService`` when
# ``LLMConfig.thinking_enabled`` is on and the caller passed a
# ``task_category`` without an explicit ``effort``: hard planning/reasoning
# gets a real scratchpad, high-volume classification/embedding stays off.
DEFAULT_EFFORT_BY_TASK_CATEGORY: dict[str, EffortLevel] = {
    "planning": EffortLevel.HIGH,
    "reasoning": EffortLevel.HIGH,
    "execution": EffortLevel.MEDIUM,
    "summarization": EffortLevel.LOW,
    "classification": EffortLevel.OFF,
    "embedding": EffortLevel.OFF,
}


def effort_for_category(task_category: str | None) -> EffortLevel | None:
    """Default :class:`EffortLevel` for a task category, or None if unknown."""
    if not task_category:
        return None
    return DEFAULT_EFFORT_BY_TASK_CATEGORY.get(task_category.strip().lower())


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
    level = _coerce_effort(effort)
    if thinking_budget is not None and thinking_budget > 0:
        budget = thinking_budget
        # An explicit budget overrides the tier for the legacy form, but the
        # tier is still what an adaptive family is told, so it is kept only
        # when the caller actually asked for one.
        level = level if level is not None and level is not EffortLevel.OFF else None
    elif level is not None:
        budget = budget_for_effort(level)

    if budget <= 0:
        return ThinkingPlan(enabled=False, budget_tokens=0, max_tokens=max_tokens)

    required = budget + _ANSWER_HEADROOM_TOKENS
    return ThinkingPlan(
        enabled=True,
        budget_tokens=budget,
        max_tokens=max(max_tokens, required),
        effort=level,
    )
