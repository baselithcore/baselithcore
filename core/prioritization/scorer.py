"""
Task Prioritizer - Scoring System

Calculates priority scores based on multiple factors.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core.config.prioritization import PrioritizationConfig

from .models import Task


@dataclass
class PriorityScore:
    """Priority score with breakdown."""

    total: float  # 0.0 to 1.0
    urgency_component: float
    importance_component: float
    effort_component: float
    deadline_component: float
    dependency_component: float


class TaskPrioritizer:
    """
    Calculates task priority based on multiple weighted factors.

    Factors:
    - Urgency: How soon does this need attention?
    - Importance: How critical is this task?
    - Effort: Favor lower effort tasks (quick wins)
    - Deadline: Time until deadline
    - Dependencies: Prioritize tasks that unblock others
    """

    def __init__(
        self,
        config: Optional["PrioritizationConfig"] = None,
        weight_urgency: Optional[float] = None,
        weight_importance: Optional[float] = None,
        weight_effort: Optional[float] = None,
        weight_deadline: Optional[float] = None,
        weight_dependencies: Optional[float] = None,
    ):
        """
        Initialize prioritizer with factor weights.

        Args:
            config: Configuration object (preferred)
            weight_*: Overrides for specific weights (legacy)
        """
        if config:
            self.config = config
        else:
            # If no config provided, creating one.
            # If explicit weights are provided, use them; otherwise let Config use defaults/env.
            # Note: To maintain exact legacy behavior (hardcoded defaults ignoring env unless Config is used),
            # we would need to pass the hardcoded defaults.
            # However, moving to Config implies we WANT env vars to take effect.
            # So we will use the Config defaults, but allow overrides via args.

            # Helper to pick arg or default
            # We explicitly pass the args to the Config if they are not None.
            config_kwargs = {}
            if weight_urgency is not None:
                config_kwargs["weight_urgency"] = weight_urgency
            if weight_importance is not None:
                config_kwargs["weight_importance"] = weight_importance
            if weight_effort is not None:
                config_kwargs["weight_effort"] = weight_effort
            if weight_deadline is not None:
                config_kwargs["weight_deadline"] = weight_deadline
            if weight_dependencies is not None:
                config_kwargs["weight_dependencies"] = weight_dependencies

            # If no args provided, we might want to respect the OLD default values if they differ from Config defaults.
            # But here they are the same (0.25, 0.30, etc).
            # So we can just instantiate Config.
            from core.config.prioritization import PrioritizationConfig

            self.config = PrioritizationConfig(**config_kwargs)

        # Map config values to instance attributes for backward compatibility/internal use
        self.weight_urgency = self.config.weight_urgency
        self.weight_importance = self.config.weight_importance
        self.weight_effort = self.config.weight_effort
        self.weight_deadline = self.config.weight_deadline
        self.weight_dependencies = self.config.weight_dependencies

    def score(
        self,
        task: Task,
        dependent_count: int = 0,
        max_dependents: int = 10,
    ) -> PriorityScore:
        """
        Calculate priority score for a task.

        Args:
            task: Task to score
            dependent_count: Number of tasks depending on this one
            max_dependents: Maximum dependents for normalization

        Returns:
            PriorityScore with total and component breakdown
        """
        # Component scores
        urgency = task.urgency
        importance = task.importance

        # Effort: invert so lower effort = higher priority
        effort = 1.0 - task.effort

        # Deadline score
        deadline = self._calculate_deadline_score(task.deadline)

        # Dependency score: more dependents = higher priority
        dependency = min(1.0, dependent_count / max(1, max_dependents))

        # Weighted total
        total = (
            urgency * self.weight_urgency
            + importance * self.weight_importance
            + effort * self.weight_effort
            + deadline * self.weight_deadline
            + dependency * self.weight_dependencies
        )

        return PriorityScore(
            total=min(1.0, max(0.0, total)),
            urgency_component=urgency * self.weight_urgency,
            importance_component=importance * self.weight_importance,
            effort_component=effort * self.weight_effort,
            deadline_component=deadline * self.weight_deadline,
            dependency_component=dependency * self.weight_dependencies,
        )

    def _calculate_deadline_score(
        self,
        deadline: Optional[datetime],
        max_days: float = 30.0,
    ) -> float:
        """
        Calculate deadline urgency score.

        Returns 1.0 for overdue, decreasing to 0.0 at max_days out.
        """
        if deadline is None:
            return 0.5  # Neutral score for no deadline

        now = datetime.now()
        days_remaining = (deadline - now).total_seconds() / 86400

        if days_remaining <= 0:
            return 1.0  # Overdue = max urgency
        elif days_remaining >= max_days:
            return 0.0  # Far in future = low urgency
        else:
            return 1.0 - (days_remaining / max_days)
