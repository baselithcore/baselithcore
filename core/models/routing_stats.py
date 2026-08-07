"""Outcome-fed routing scoreboard.

A static routing table encodes what someone believed at design time. The
production traffic knows better: if a cheap model clears 99% of the log
parsing, there is no reason to keep paying flagship prices for it, and if a
model silently degrades on a category, the table is the last place anyone
looks.

This module turns the table into a scoreboard that updates itself from
recorded outcomes, with the two guards that keep it from being worse than
the static default:

* a **minimum-sample guard** — one lucky run cannot get an architecture
  review downgraded;
* a **margin** — a challenger must beat the incumbent by a real gap, not by
  measurement noise, before the routing changes.

The scoreboard never picks a model on its own: it can only prefer one of the
candidates the policy already considers legitimate for that category.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from core.models.routing import (
    Complexity,
    ModelRouter,
    RoutingDecision,
    RoutingPolicy,
    TaskCategory,
)
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["LearnedModelRouter", "RouteStats", "RoutingScoreboard"]


@dataclass
class RouteStats:
    """Recorded outcomes for one ``(category, model)`` pair."""

    attempts: int = 0
    successes: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    @property
    def success_rate(self) -> float:
        """Fraction of successful attempts (0.0 when never attempted)."""
        return self.successes / self.attempts if self.attempts else 0.0

    @property
    def avg_cost_usd(self) -> float:
        """Mean cost per attempt."""
        return self.cost_usd / self.attempts if self.attempts else 0.0

    @property
    def avg_latency_ms(self) -> float:
        """Mean latency per attempt."""
        return self.latency_ms / self.attempts if self.attempts else 0.0


@dataclass
class RoutingScoreboard:
    """Per-``(category, model)`` success tracking with confidence guards.

    Args:
        min_samples: Attempts a pair needs before it is allowed to influence
            routing at all.
        margin: How much better a challenger's success rate must be than the
            incumbent's to justify overriding the static policy.

    The scoreboard is thread-safe: recording happens on request paths that
    may run concurrently.
    """

    min_samples: int = 20
    margin: float = 0.05
    _stats: dict[tuple[TaskCategory, str], RouteStats] = field(
        default_factory=dict, init=False, repr=False
    )
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if not 0.0 <= self.margin <= 1.0:
            raise ValueError("margin must be within [0.0, 1.0]")

    def record(
        self,
        category: TaskCategory,
        model_id: str,
        *,
        success: bool,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
    ) -> None:
        """Record one observed outcome.

        Args:
            category: Task bucket the call belonged to.
            model_id: Model that served the call.
            success: Whether the call met its acceptance criterion. Define
                that criterion outside this module — the scoreboard trusts
                what it is told, so a lenient verifier produces a lenient
                routing table.
            cost_usd: Observed cost, for reporting.
            latency_ms: Observed latency, for reporting.
        """
        key = (category, model_id)
        with self._lock:
            stats = self._stats.setdefault(key, RouteStats())
            stats.attempts += 1
            stats.successes += int(success)
            stats.cost_usd += max(0.0, cost_usd)
            stats.latency_ms += max(0, latency_ms)

    def stats_for(self, category: TaskCategory, model_id: str) -> RouteStats | None:
        """Return the recorded stats for a pair, or None if never recorded."""
        with self._lock:
            stats = self._stats.get((category, model_id))
            return RouteStats(**vars(stats)) if stats is not None else None

    def candidates(self, category: TaskCategory) -> dict[str, RouteStats]:
        """Return every model recorded for *category*, copied."""
        with self._lock:
            return {
                model: RouteStats(**vars(stats))
                for (cat, model), stats in self._stats.items()
                if cat == category
            }

    def prefer(
        self,
        category: TaskCategory,
        incumbent: str,
        allowed: set[str] | None = None,
    ) -> str | None:
        """Suggest a better model than *incumbent* for *category*.

        Args:
            category: Task bucket being routed.
            incumbent: Model the static policy chose.
            allowed: Optional restriction to models the deployment permits.
                Anything outside it is ignored, so the scoreboard can never
                route to a model the policy would not have considered.

        Returns:
            The challenger's model id, or None when no candidate has enough
            samples or a wide enough margin. Returning None is the common
            case by design — the static policy stays in charge until the
            evidence is unambiguous.
        """
        pool = self.candidates(category)
        incumbent_stats = pool.get(incumbent)
        # An incumbent with too few samples is not a baseline worth beating.
        incumbent_rate = (
            incumbent_stats.success_rate
            if incumbent_stats and incumbent_stats.attempts >= self.min_samples
            else None
        )

        best: tuple[str, float] | None = None
        for model, stats in pool.items():
            if model == incumbent or stats.attempts < self.min_samples:
                continue
            if allowed is not None and model not in allowed:
                continue
            if (
                incumbent_rate is not None
                and stats.success_rate < incumbent_rate + self.margin
            ):
                continue
            if incumbent_rate is None and stats.success_rate < 1.0 - self.margin:
                # No trustworthy baseline: only a near-perfect challenger may
                # move traffic, and even then it must clear min_samples.
                continue
            if best is None or stats.success_rate > best[1]:
                best = (model, stats.success_rate)

        return best[0] if best else None

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        """Export the scoreboard for logging or a dashboard."""
        with self._lock:
            return {
                f"{category.value}:{model}": {
                    "attempts": stats.attempts,
                    "successes": stats.successes,
                    "success_rate": round(stats.success_rate, 4),
                    "avg_cost_usd": round(stats.avg_cost_usd, 6),
                    "avg_latency_ms": round(stats.avg_latency_ms, 2),
                }
                for (category, model), stats in self._stats.items()
            }


class LearnedModelRouter(ModelRouter):
    """A :class:`ModelRouter` whose picks a scoreboard may override.

    The static policy always decides first; the scoreboard only gets to
    substitute a model that (a) the policy already lists for some category,
    (b) has cleared ``min_samples``, and (c) beats the incumbent by the
    configured margin. Every override is reported through
    ``RoutingDecision.rule == "learned_override"``, so an audit can tell a
    policy decision from a learned one.

    Args:
        policy: Static routing policy.
        scoreboard: Outcome scoreboard. Passing None makes this class behave
            exactly like :class:`ModelRouter`.
    """

    def __init__(
        self,
        policy: RoutingPolicy | None = None,
        scoreboard: RoutingScoreboard | None = None,
    ) -> None:
        super().__init__(policy)
        self._scoreboard = scoreboard

    @property
    def scoreboard(self) -> RoutingScoreboard | None:
        """The attached scoreboard, if any."""
        return self._scoreboard

    def _allowed_models(self) -> set[str]:
        """Every model the static policy can produce."""
        allowed = set(self.policy.primary.values())
        for upgrades in self.policy.complexity_upgrade.values():
            allowed.update(upgrades.values())
        return allowed

    def select(
        self,
        category: TaskCategory,
        complexity: Complexity = Complexity.MEDIUM,
    ) -> RoutingDecision:
        """Route the task, letting the scoreboard override when confident."""
        decision = super().select(category, complexity)
        if self._scoreboard is None:
            return decision

        challenger = self._scoreboard.prefer(
            category, decision.model_id, allowed=self._allowed_models()
        )
        if challenger is None:
            return decision

        logger.info(
            "Learned routing override for %s: %s -> %s",
            category.value,
            decision.model_id,
            challenger,
        )
        return RoutingDecision(
            model_id=challenger,
            rule="learned_override",
            category=category,
            complexity=complexity,
        )
