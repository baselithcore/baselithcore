"""
Autonomy spectrum for the orchestrator.

Implements the spectrum from book ch10: SUPERVISED → SEMI_AUTONOMOUS →
FULLY_AUTONOMOUS. Each level dictates which tool categories require human
approval. Upgrade is gated by evaluation pass rate.

Integration hook: ``Orchestrator`` consults ``AutonomyPolicy.requires_approval``
before invoking any mutating tool. ``AutonomyUpgradeGate`` is consulted by
operators (CLI / admin endpoint) to advance the level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Final


class AutonomyLevel(IntEnum):
    """Discrete autonomy levels, ordered by trust."""

    SUPERVISED = 0
    SEMI_AUTONOMOUS = 1
    FULLY_AUTONOMOUS = 2


class ToolCategory(str):
    """Coarse-grained tool categories used by the approval matrix."""


READ_ONLY: Final[str] = "read_only"
MUTATING: Final[str] = "mutating"
DESTRUCTIVE: Final[str] = "destructive"
EXTERNAL_SIDE_EFFECT: Final[str] = "external_side_effect"

ALL_CATEGORIES: Final[frozenset[str]] = frozenset(
    {READ_ONLY, MUTATING, DESTRUCTIVE, EXTERNAL_SIDE_EFFECT}
)


_APPROVAL_MATRIX: Final[dict[AutonomyLevel, frozenset[str]]] = {
    AutonomyLevel.SUPERVISED: frozenset({MUTATING, DESTRUCTIVE, EXTERNAL_SIDE_EFFECT}),
    AutonomyLevel.SEMI_AUTONOMOUS: frozenset({DESTRUCTIVE, EXTERNAL_SIDE_EFFECT}),
    AutonomyLevel.FULLY_AUTONOMOUS: frozenset(),
}


_UPGRADE_THRESHOLDS: Final[dict[AutonomyLevel, float]] = {
    AutonomyLevel.SEMI_AUTONOMOUS: 0.90,
    AutonomyLevel.FULLY_AUTONOMOUS: 0.98,
}


@dataclass(frozen=True)
class AutonomyPolicy:
    """Runtime policy: which tool categories need approval at this level."""

    level: AutonomyLevel = AutonomyLevel.SUPERVISED
    require_audit_log: bool = True

    def requires_approval(self, category: str) -> bool:
        """Return True if a tool of the given category needs human approval."""
        if category not in ALL_CATEGORIES:
            raise ValueError(
                f"Unknown tool category '{category}'. "
                f"Expected one of {sorted(ALL_CATEGORIES)}"
            )
        return category in _APPROVAL_MATRIX[self.level]


@dataclass
class AutonomyUpgradeGate:
    """Decides whether an upgrade to a higher level is permitted."""

    red_team_pass_rate: float = 1.0
    eval_pass_rate: float = 0.0
    successful_runs: int = 0
    minimum_runs: int = 50
    blockers: list[str] = field(default_factory=list)

    def can_upgrade_to(self, target: AutonomyLevel) -> tuple[bool, list[str]]:
        """Return ``(allowed, reasons_blocked)`` for a proposed upgrade."""
        reasons: list[str] = list(self.blockers)
        threshold = _UPGRADE_THRESHOLDS.get(target)
        if threshold is None:
            reasons.append(f"no upgrade path defined for {target.name}")
            return False, reasons
        if self.eval_pass_rate < threshold:
            reasons.append(
                f"eval_pass_rate={self.eval_pass_rate:.2f} < {threshold:.2f}"
            )
        if self.red_team_pass_rate < 1.0:
            reasons.append(f"red_team_pass_rate={self.red_team_pass_rate:.2f} < 1.00")
        if self.successful_runs < self.minimum_runs:
            reasons.append(
                f"successful_runs={self.successful_runs} < {self.minimum_runs}"
            )
        return (len(reasons) == 0), reasons
