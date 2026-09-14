"""
Cost-aware model router.

Picks the model that fits the task complexity rather than using the most
capable model for every call. Planning and adversarial reasoning go to a
flagship model; execution, classification, and short summaries go to a
small/cheap model.

The router is policy-driven and provider-agnostic. Tasks are typed via
``TaskCategory``; deployments override the default mapping by passing a
custom ``policy``. Routing decisions and their rationale are exposed via
``RoutingDecision`` so they can be logged and audited.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Final


class TaskCategory(str, Enum):
    """High-level task buckets used to pick a model tier."""

    PLANNING = "planning"
    REASONING = "reasoning"
    EXECUTION = "execution"
    CLASSIFICATION = "classification"
    SUMMARIZATION = "summarization"
    EMBEDDING = "embedding"


class Complexity(str, Enum):
    """Coarse difficulty signal used to break ties inside a category."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


@dataclass(frozen=True)
class RoutingDecision:
    """The outcome of a single routing call, plus its rationale."""

    model_id: str
    rule: str
    category: TaskCategory
    complexity: Complexity


_DEFAULT_PRIMARY: Final[Mapping[TaskCategory, str]] = {
    TaskCategory.PLANNING: "claude-opus-5",
    TaskCategory.REASONING: "claude-opus-5",
    TaskCategory.EXECUTION: "claude-sonnet-5",
    TaskCategory.CLASSIFICATION: "claude-haiku-4-5",
    TaskCategory.SUMMARIZATION: "claude-haiku-4-5",
    TaskCategory.EMBEDDING: "claude-haiku-4-5",
}

_COMPLEXITY_UPGRADE: Final[Mapping[TaskCategory, dict[Complexity, str]]] = {
    TaskCategory.EXECUTION: {Complexity.COMPLEX: "claude-opus-5"},
    TaskCategory.SUMMARIZATION: {Complexity.COMPLEX: "claude-sonnet-5"},
    TaskCategory.CLASSIFICATION: {Complexity.COMPLEX: "claude-sonnet-5"},
}


def cost_per_1k_usd(model_id: str) -> float:
    """Rough USD cost of 1K tokens for ``model_id`` (500 in / 500 out).

    Used only to *rank* and *bound* candidates for the ``max_cost_per_1k_usd``
    guard below, not for billing (real calls are priced by
    :func:`core.models.pricing.estimate_cost` from actual token counts).

    Public because the guard has a second enforcement point: the learned router
    (:meth:`core.models.routing_stats.LearnedModelRouter._allowed_models`) has
    to apply the same ceiling to the scoreboard's candidate set, and a
    cross-module import of a private name is how two copies of a rule drift
    apart.
    """
    from core.models.pricing import get_price

    return get_price(model_id).estimate(500, 500)


@dataclass
class RoutingPolicy:
    """Customizable policy. Default behaviour is production-safe."""

    primary: Mapping[TaskCategory, str] = field(
        default_factory=lambda: dict(_DEFAULT_PRIMARY)
    )
    complexity_upgrade: Mapping[TaskCategory, dict[Complexity, str]] = field(
        default_factory=lambda: {cat: dict(m) for cat, m in _COMPLEXITY_UPGRADE.items()}
    )

    def _candidate_pool(self) -> tuple[str, ...]:
        """Every distinct model id this policy might ever select."""
        ids = set(self.primary.values())
        for tier in self.complexity_upgrade.values():
            ids.update(tier.values())
        return tuple(ids)

    def _resolve(
        self, category: TaskCategory, complexity: Complexity
    ) -> RoutingDecision:
        upgrade = self.complexity_upgrade.get(category, {}).get(complexity)
        if upgrade is not None:
            return RoutingDecision(
                model_id=upgrade,
                rule="complexity_upgrade",
                category=category,
                complexity=complexity,
            )
        primary = self.primary.get(category)
        if primary is None:
            raise KeyError(f"no primary model configured for category {category}")
        return RoutingDecision(
            model_id=primary,
            rule="primary",
            category=category,
            complexity=complexity,
        )

    def select(
        self,
        category: TaskCategory,
        complexity: Complexity,
        *,
        max_cost_per_1k_usd: float | None = None,
    ) -> RoutingDecision:
        """Return the model id and rationale for the given task signal.

        Args:
            category: Task bucket being routed.
            complexity: Difficulty signal used to break ties within the
                category.
            max_cost_per_1k_usd: Optional budget hint. When given and the
                resolved model's approximate cost per 1K tokens (500 in /
                500 out, per :func:`cost_per_1k_usd`) exceeds it, the
                *priciest* candidate in this policy's pool that still fits
                the budget is substituted instead (``rule="cost_guard"``) —
                the best quality the budget affords, not just any affordable
                model. When no candidate fits, the single cheapest candidate
                is returned as a best effort rather than raising.
        """
        decision = self._resolve(category, complexity)
        if max_cost_per_1k_usd is None:
            return decision
        if cost_per_1k_usd(decision.model_id) <= max_cost_per_1k_usd:
            return decision

        candidates = sorted(self._candidate_pool(), key=cost_per_1k_usd)
        if not candidates:
            return decision
        affordable = [
            c for c in candidates if cost_per_1k_usd(c) <= max_cost_per_1k_usd
        ]
        if affordable:
            best = max(affordable, key=cost_per_1k_usd)
            return RoutingDecision(
                model_id=best,
                rule="cost_guard",
                category=category,
                complexity=complexity,
            )
        # Nothing fits the budget: fall back to the cheapest known candidate
        # rather than raise — a routing hint should never abort a request.
        return RoutingDecision(
            model_id=candidates[0],
            rule="cost_guard",
            category=category,
            complexity=complexity,
        )


class ModelRouter:
    """Thin facade over a ``RoutingPolicy``."""

    def __init__(self, policy: RoutingPolicy | None = None) -> None:
        self._policy = policy or RoutingPolicy()

    def select(
        self,
        category: TaskCategory,
        complexity: Complexity = Complexity.MEDIUM,
        *,
        max_cost_per_1k_usd: float | None = None,
    ) -> RoutingDecision:
        return self._policy.select(
            category, complexity, max_cost_per_1k_usd=max_cost_per_1k_usd
        )

    @property
    def policy(self) -> RoutingPolicy:
        return self._policy
