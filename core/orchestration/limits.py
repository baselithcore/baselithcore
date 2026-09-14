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
from typing import Any, Final

from core.observability.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MAX_ITERATIONS: Final[int] = 25
DEFAULT_BUDGET_USD: Final[float] = 0.50
DEFAULT_MAX_TOOL_CALLS: Final[int] = 50
#: Fallback token ceiling for one request when the orchestration settings are
#: unreadable. A cap that is never set is not a cap: the historical ``None``
#: default left every loop token-unbounded *and* pinned ``token_pressure()`` at
#: 0.0, so the context auto-tuning that polls it could never fire.
DEFAULT_MAX_TOKENS: Final[int] = 400_000
#: Fallback wall-clock deadline (seconds) for one request.
DEFAULT_MAX_SECONDS: Final[float] = 600.0
#: Assumed model context window; denominator of :meth:`LoopBudget.token_pressure`
#: when a caller explicitly disabled the token cap.
DEFAULT_CONTEXT_WINDOW_TOKENS: Final[int] = 200_000


def _orchestration_settings() -> Any | None:
    """The orchestration settings, or ``None`` when they cannot be read.

    Settings resolution must never be the reason a budget cannot be built, so
    every failure degrades to the module-level fallbacks above.
    """
    try:
        from core.config.orchestration import get_orchestration_config

        return get_orchestration_config()
    except Exception:  # pragma: no cover - defensive: config must not break loops
        logger.debug("orchestration_settings_unavailable", exc_info=True)
        return None


def _default_max_tokens() -> int | None:
    """Configured cumulative token cap; ``None`` when explicitly disabled (0)."""
    config = _orchestration_settings()
    raw = getattr(config, "loop_max_tokens", DEFAULT_MAX_TOKENS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS
    return value if value > 0 else None


def _default_max_seconds() -> float | None:
    """Configured wall-clock deadline; ``None`` when explicitly disabled (0)."""
    config = _orchestration_settings()
    raw = getattr(config, "loop_max_seconds", DEFAULT_MAX_SECONDS)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_SECONDS
    return value if value > 0 else None


def _context_window_tokens() -> int:
    """Configured context window used by the token-pressure fallback."""
    config = _orchestration_settings()
    raw = getattr(config, "context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CONTEXT_WINDOW_TOKENS
    return max(value, 0)


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
    # LLM call). Defaults to ``ORCHESTRATOR_LOOP_MAX_TOKENS``; None disables
    # token enforcement (and switches token_pressure() to the context-window
    # fallback below).
    max_tokens: int | None = field(default_factory=_default_max_tokens)
    # Wall-clock deadline for the whole request, in seconds from budget
    # creation. Defaults to ``ORCHESTRATOR_LOOP_MAX_SECONDS``; None disables
    # it. Checked on every tick; use ``LoopBudget.remaining_seconds()`` to
    # derive per-call timeouts so a single slow tool or LLM call can't outlive
    # the request deadline.
    max_seconds: float | None = field(default_factory=_default_max_seconds)


@dataclass
class LoopBudgetSnapshot:
    """Immutable snapshot of budget state at a given tick."""

    iterations: int
    tool_calls: int
    cost_usd: float
    tokens: int = 0
    elapsed_seconds: float = 0.0
    #: Tokens of assembled context (recalled memory + recent history) injected
    #: into the request. A *label on a subset* of ``tokens``, not an extra
    #: charge — see :meth:`LoopBudget.record_context_tokens`.
    context_tokens: int = 0

    def __str__(self) -> str:
        return (
            f"iter={self.iterations} tool_calls={self.tool_calls} "
            f"cost_usd={self.cost_usd:.4f} tokens={self.tokens} "
            f"context_tokens={self.context_tokens} "
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
    context_tokens: int = 0
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

    def record_context_tokens(self, count: int) -> None:
        """Record tokens of assembled context injected into the request.

        Deliberately **not** charged against ``tokens`` or ``max_tokens``: the
        injected block travels inside the prompt of the LLM calls that
        ``record_tokens`` already counts, so charging it here would double-bill
        and could abort a request that never exceeded its real budget. This is
        an *allocation* measurement — how much of the budget went to static
        recall instead of dynamic reasoning — and it is what makes the
        memory-vs-computation trade-off tunable with data rather than guesses.

        Negative or zero counts are ignored.
        """
        if count <= 0:
            return
        self.context_tokens += count

    def context_share(self) -> float:
        """Fraction of the request's tokens attributable to injected context.

        ``0.0`` before the first LLM call (nothing to compare against) and
        clamped to ``1.0``: the context block rides along in *every*
        iteration's prompt while it is measured once, so on a multi-step run
        the raw ratio can exceed one.
        """
        if self.tokens <= 0:
            return 0.0
        return min(self.context_tokens / self.tokens, 1.0)

    def token_pressure(self) -> float:
        """Fraction of the available token room consumed, in ``[0, 1]``.

        With a token cap set (the default), this is ``tokens / max_tokens``.
        When a caller explicitly disabled the cap the signal must not silently
        vanish — a pressure that is structurally 0.0 makes every consumer of it
        dead code — so it falls back to the assembled context measured against
        the configured context window (``ORCHESTRATOR_CONTEXT_WINDOW_TOKENS``).

        Handlers poll this to trigger context compaction *before* a hard cap
        aborts the request (e.g. compact when ``token_pressure() > 0.8``).
        """
        cap = self.limits.max_tokens
        if cap:
            return min(self.tokens / cap, 1.0)
        window = _context_window_tokens()
        if window <= 0 or self.context_tokens <= 0:
            return 0.0
        return min(self.context_tokens / window, 1.0)

    def snapshot(self) -> LoopBudgetSnapshot:
        return LoopBudgetSnapshot(
            iterations=self.iterations,
            tool_calls=self.tool_calls,
            cost_usd=self.cost_usd,
            tokens=self.tokens,
            elapsed_seconds=self.elapsed_seconds(),
            context_tokens=self.context_tokens,
        )
