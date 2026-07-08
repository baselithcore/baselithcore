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

import time
from dataclasses import dataclass, field
from typing import Final

from core.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_ITERATIONS: Final[int] = 25
DEFAULT_BUDGET_USD: Final[float] = 0.50
DEFAULT_MAX_TOOL_CALLS: Final[int] = 50
# Token cap defaults to None (disabled) — a token budget is model/context-window
# specific, so callers opt in with an explicit cap rather than inheriting one.
DEFAULT_MAX_TOKENS: Final[int | None] = None


class BudgetExceededError(RuntimeError):
    """Raised when iteration or cost cap is exceeded mid-loop."""

    def __init__(self, reason: str, snapshot: LoopBudgetSnapshot) -> None:
        super().__init__(f"Loop budget exceeded: {reason} | {snapshot}")
        self.reason = reason
        self.snapshot = snapshot


@dataclass(frozen=True)
class LoopLimits:
    """Static caps for a single orchestrator request."""

    max_iterations: int = DEFAULT_MAX_ITERATIONS
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS
    budget_usd: float = DEFAULT_BUDGET_USD
    # Cumulative token cap for the whole request (input + output across every
    # LLM call). None disables token enforcement.
    max_tokens: int | None = DEFAULT_MAX_TOKENS
    # Wall-clock deadline for the whole request, in seconds from budget
    # creation. None disables it. Checked on every tick; use
    # ``LoopBudget.remaining_seconds()`` to derive per-call timeouts so a
    # single slow tool or LLM call can't outlive the request deadline.
    max_seconds: float | None = None


@dataclass
class LoopBudgetSnapshot:
    """Immutable snapshot of budget state at a given tick."""

    iterations: int
    tool_calls: int
    cost_usd: float
    tokens: int = 0
    elapsed_seconds: float = 0.0

    def __str__(self) -> str:
        return (
            f"iter={self.iterations} tool_calls={self.tool_calls} "
            f"cost_usd={self.cost_usd:.4f} tokens={self.tokens} "
            f"elapsed={self.elapsed_seconds:.1f}s"
        )


@dataclass
class LoopBudget:
    """Mutable per-request budget tracker."""

    limits: LoopLimits = field(default_factory=LoopLimits)
    iterations: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    tokens: int = 0
    # Monotonic start time; basis for the wall-clock deadline.
    started_at: float = field(default_factory=time.monotonic)

    def elapsed_seconds(self) -> float:
        """Wall-clock seconds since the budget was created."""
        return time.monotonic() - self.started_at

    def remaining_seconds(self) -> float | None:
        """Seconds left before the deadline, or None when no deadline is set.

        Clamped at 0.0 — suitable to pass directly as an ``asyncio.wait_for``
        timeout for the next tool/LLM call.
        """
        cap = self.limits.max_seconds
        if cap is None:
            return None
        return max(0.0, cap - self.elapsed_seconds())

    def check_deadline(self) -> None:
        """Raise when the wall-clock deadline has passed. No-op without one."""
        cap = self.limits.max_seconds
        if cap is not None and self.elapsed_seconds() > cap:
            raise BudgetExceededError("max_seconds", self.snapshot())

    def tick(self) -> None:
        """Advance one iteration. Raises if iteration or deadline cap reached."""
        self.check_deadline()
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

    def record_tokens(self, count: int) -> None:
        """Add token usage; raise if the token cap is exceeded.

        Negative counts are ignored (clamped to 0) so a bad estimate can't
        under-count the running total. No-op when ``max_tokens`` is None.
        """
        if count <= 0:
            return
        self.tokens += count
        cap = self.limits.max_tokens
        if cap is not None and self.tokens > cap:
            raise BudgetExceededError("max_tokens", self.snapshot())

    def token_pressure(self) -> float:
        """Fraction of the token cap consumed, in ``[0, 1]``.

        Returns 0.0 when no token cap is set. Handlers can poll this to trigger
        context compaction *before* the hard cap aborts the request (e.g.
        compact when ``token_pressure() > 0.8``).
        """
        cap = self.limits.max_tokens
        if not cap:
            return 0.0
        return min(self.tokens / cap, 1.0)

    def snapshot(self) -> LoopBudgetSnapshot:
        return LoopBudgetSnapshot(
            iterations=self.iterations,
            tool_calls=self.tool_calls,
            cost_usd=self.cost_usd,
            tokens=self.tokens,
            elapsed_seconds=self.elapsed_seconds(),
        )
