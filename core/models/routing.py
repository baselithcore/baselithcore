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
    TaskCategory.PLANNING: "claude-opus-4-7",
    TaskCategory.REASONING: "claude-opus-4-7",
    TaskCategory.EXECUTION: "claude-sonnet-4-6",
    TaskCategory.CLASSIFICATION: "claude-haiku-4-5",
    TaskCategory.SUMMARIZATION: "claude-haiku-4-5",
    TaskCategory.EMBEDDING: "claude-haiku-4-5",
}

_COMPLEXITY_UPGRADE: Final[Mapping[TaskCategory, dict[Complexity, str]]] = {
    TaskCategory.EXECUTION: {Complexity.COMPLEX: "claude-opus-4-7"},
    TaskCategory.SUMMARIZATION: {Complexity.COMPLEX: "claude-sonnet-4-6"},
    TaskCategory.CLASSIFICATION: {Complexity.COMPLEX: "claude-sonnet-4-6"},
}


@dataclass
class RoutingPolicy:
    """Customizable policy. Default behaviour is production-safe."""

    primary: Mapping[TaskCategory, str] = field(
        default_factory=lambda: dict(_DEFAULT_PRIMARY)
    )
    complexity_upgrade: Mapping[TaskCategory, dict[Complexity, str]] = field(
        default_factory=lambda: {cat: dict(m) for cat, m in _COMPLEXITY_UPGRADE.items()}
    )

    def select(self, category: TaskCategory, complexity: Complexity) -> RoutingDecision:
        """Return the model id and rationale for the given task signal."""
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


class ModelRouter:
    """Thin facade over a ``RoutingPolicy``."""

    def __init__(self, policy: RoutingPolicy | None = None) -> None:
        self._policy = policy or RoutingPolicy()

    def select(
        self,
        category: TaskCategory,
        complexity: Complexity = Complexity.MEDIUM,
    ) -> RoutingDecision:
        return self._policy.select(category, complexity)

    @property
    def policy(self) -> RoutingPolicy:
        return self._policy
