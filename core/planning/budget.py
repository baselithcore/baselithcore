"""
Budgetary Constraints for Plan Orchestration.

Provides types and logic for enforcing resource limits (tokens, latency,
step counts) during the planning and execution phases, ensuring
predictable operational costs.
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class PlanningBudget:
    """
    Resource allocation profile for task planning.

    Defines the upper bounds for an execution cycle, allowing the planner
    to optimize for efficiency and avoid runaway loops or excessive API
    consumption.
    """

    max_steps: int = 10
    """Maximum number of steps in the plan."""

    max_estimated_tokens: int = 10000
    """Estimated token budget for plan execution."""

    max_tool_calls: int = 20
    """Maximum number of tool invocations allowed."""

    max_latency_ms: int = 30000
    """Maximum acceptable latency in milliseconds."""

    cost_per_step: float = 100.0
    """Estimated token cost per step (for budget calculation)."""

    cost_per_tool_call: float = 50.0
    """Estimated token cost per tool call."""

    def remaining_budget(
        self,
        steps_used: int = 0,
        tokens_used: int = 0,
        tool_calls_used: int = 0,
    ) -> "BudgetRemaining":
        """
        Calculate remaining budget after partial execution.

        Args:
            steps_used: Steps already executed
            tokens_used: Tokens already consumed
            tool_calls_used: Tool calls already made

        Returns:
            BudgetRemaining with remaining allocations
        """
        return BudgetRemaining(
            steps=max(0, self.max_steps - steps_used),
            tokens=max(0, self.max_estimated_tokens - tokens_used),
            tool_calls=max(0, self.max_tool_calls - tool_calls_used),
        )

    def is_exhausted(
        self,
        steps_used: int = 0,
        tokens_used: int = 0,
        tool_calls_used: int = 0,
    ) -> bool:
        """Check if budget is exhausted."""
        remaining = self.remaining_budget(steps_used, tokens_used, tool_calls_used)
        return remaining.steps <= 0 or remaining.tokens <= 0


@dataclass
class BudgetRemaining:
    """Remaining budget allocations."""

    steps: int
    tokens: int
    tool_calls: int

    @property
    def can_continue(self) -> bool:
        """Check if there's budget to continue."""
        return self.steps > 0 and self.tokens > 0


@dataclass
class StepCostEstimate:
    """Estimated cost for a plan step."""

    step_id: str
    estimated_tokens: int = 0
    estimated_tool_calls: int = 0
    estimated_latency_ms: int = 0
    confidence: float = 0.5  # 0-1 confidence in estimate

    @property
    def total_estimated_cost(self) -> float:
        """Calculate total estimated cost (normalized)."""
        return self.estimated_tokens + (self.estimated_tool_calls * 50)


@dataclass
class PlanCostEstimate:
    """Aggregated cost estimate for an entire plan."""

    total_tokens: int = 0
    total_tool_calls: int = 0
    total_latency_ms: int = 0
    step_estimates: Dict[str, StepCostEstimate] = field(default_factory=dict)

    def fits_budget(self, budget: PlanningBudget) -> bool:
        """Check if estimated costs fit within budget."""
        return (
            self.total_tokens <= budget.max_estimated_tokens
            and self.total_tool_calls <= budget.max_tool_calls
            and self.total_latency_ms <= budget.max_latency_ms
        )

    def budget_utilization(self, budget: PlanningBudget) -> Dict[str, float]:
        """Calculate budget utilization percentages."""
        return {
            "tokens": self.total_tokens / budget.max_estimated_tokens
            if budget.max_estimated_tokens > 0
            else 0,
            "tool_calls": self.total_tool_calls / budget.max_tool_calls
            if budget.max_tool_calls > 0
            else 0,
            "latency": self.total_latency_ms / budget.max_latency_ms
            if budget.max_latency_ms > 0
            else 0,
        }
