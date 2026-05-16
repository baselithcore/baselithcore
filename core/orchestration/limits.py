"""
Iteration and cost budget enforcement for the orchestrator loop.

Enforces hard caps to prevent runaway agent loops and uncontrolled
LLM spend. Designed for injection into ExecutionMixin via a per-request
``LoopBudget`` carried on the orchestration context.

Integration hook: ``core.orchestration.mixins.execution.ExecutionMixin`` must
call ``budget.tick(...)`` before each step and ``budget.charge(...)`` after
each LLM/tool call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from core.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_ITERATIONS: Final[int] = 25
DEFAULT_BUDGET_USD: Final[float] = 0.50
DEFAULT_MAX_TOOL_CALLS: Final[int] = 50


class BudgetExceededError(RuntimeError):
    """Raised when iteration or cost cap is exceeded mid-loop."""

    def __init__(self, reason: str, snapshot: "LoopBudgetSnapshot") -> None:
        super().__init__(f"Loop budget exceeded: {reason} | {snapshot}")
        self.reason = reason
        self.snapshot = snapshot


@dataclass(frozen=True)
class LoopLimits:
    """Static caps for a single orchestrator request."""

    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    budget_usd: float = DEFAULT_BUDGET_USD


@dataclass
class LoopBudgetSnapshot:
    """Immutable snapshot of budget state at a given tick."""

    iterations: int
    tool_calls: int
    cost_usd: float

    def __str__(self) -> str:
        return (
            f"iter={self.iterations} tool_calls={self.tool_calls} "
            f"cost_usd={self.cost_usd:.4f}"
        )


@dataclass
class LoopBudget:
    """Mutable per-request budget tracker."""

    limits: LoopLimits = field(default_factory=LoopLimits)
    iterations: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0

    def tick(self) -> None:
        """Advance one iteration. Raises if iteration cap reached."""
        self.iterations += 1
        if self.iterations > self.limits.max_iterations:
            raise BudgetExceededError("max_iterations", self.snapshot())

    def record_tool_call(self) -> None:
        """Record a tool invocation. Raises if cap reached."""
        self.tool_calls += 1
        if self.tool_calls > self.limits.max_tool_calls:
            raise BudgetExceededError("max_tool_calls", self.snapshot())

    def charge(self, cost_usd: float) -> None:
        """Add cost; raise if budget exceeded."""
        if cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")
        self.cost_usd += cost_usd
        if self.cost_usd > self.limits.budget_usd:
            raise BudgetExceededError("budget_usd", self.snapshot())

    def snapshot(self) -> LoopBudgetSnapshot:
        return LoopBudgetSnapshot(
            iterations=self.iterations,
            tool_calls=self.tool_calls,
            cost_usd=self.cost_usd,
        )
