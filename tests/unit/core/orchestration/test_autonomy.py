"""Unit tests for ``core.orchestration.autonomy``."""

from __future__ import annotations

import pytest

from core.orchestration.autonomy import (
    DESTRUCTIVE,
    EXTERNAL_SIDE_EFFECT,
    MUTATING,
    READ_ONLY,
    AutonomyLevel,
    AutonomyPolicy,
    AutonomyUpgradeGate,
)


class TestAutonomyPolicy:
    def test_supervised_requires_approval_for_mutations(self) -> None:
        p = AutonomyPolicy(level=AutonomyLevel.SUPERVISED)
        assert p.requires_approval(MUTATING)
        assert p.requires_approval(DESTRUCTIVE)
        assert p.requires_approval(EXTERNAL_SIDE_EFFECT)
        assert not p.requires_approval(READ_ONLY)

    def test_semi_autonomous_allows_simple_mutations(self) -> None:
        p = AutonomyPolicy(level=AutonomyLevel.SEMI_AUTONOMOUS)
        assert not p.requires_approval(MUTATING)
        assert p.requires_approval(DESTRUCTIVE)
        assert p.requires_approval(EXTERNAL_SIDE_EFFECT)

    def test_fully_autonomous_never_requires_approval(self) -> None:
        p = AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS)
        assert not p.requires_approval(MUTATING)
        assert not p.requires_approval(DESTRUCTIVE)
        assert not p.requires_approval(EXTERNAL_SIDE_EFFECT)

    def test_unknown_category_raises(self) -> None:
        with pytest.raises(ValueError):
            AutonomyPolicy().requires_approval("nonsense")


class TestAutonomyUpgradeGate:
    def test_upgrade_blocked_when_eval_below_threshold(self) -> None:
        g = AutonomyUpgradeGate(
            eval_pass_rate=0.85,
            red_team_pass_rate=1.0,
            successful_runs=100,
        )
        ok, reasons = g.can_upgrade_to(AutonomyLevel.SEMI_AUTONOMOUS)
        assert not ok
        assert any("eval_pass_rate" in r for r in reasons)

    def test_upgrade_blocked_when_red_team_fails(self) -> None:
        g = AutonomyUpgradeGate(
            eval_pass_rate=0.95,
            red_team_pass_rate=0.99,
            successful_runs=100,
        )
        ok, reasons = g.can_upgrade_to(AutonomyLevel.SEMI_AUTONOMOUS)
        assert not ok
        assert any("red_team" in r for r in reasons)

    def test_upgrade_blocked_when_insufficient_runs(self) -> None:
        g = AutonomyUpgradeGate(
            eval_pass_rate=0.99,
            red_team_pass_rate=1.0,
            successful_runs=10,
        )
        ok, reasons = g.can_upgrade_to(AutonomyLevel.FULLY_AUTONOMOUS)
        assert not ok
        assert any("successful_runs" in r for r in reasons)

    def test_upgrade_allowed_when_thresholds_met(self) -> None:
        g = AutonomyUpgradeGate(
            eval_pass_rate=0.99,
            red_team_pass_rate=1.0,
            successful_runs=100,
        )
        ok, reasons = g.can_upgrade_to(AutonomyLevel.FULLY_AUTONOMOUS)
        assert ok
        assert reasons == []

    def test_upgrade_to_supervised_has_no_path(self) -> None:
        g = AutonomyUpgradeGate(
            eval_pass_rate=1.0,
            red_team_pass_rate=1.0,
            successful_runs=1000,
        )
        ok, reasons = g.can_upgrade_to(AutonomyLevel.SUPERVISED)
        assert not ok
        assert any("no upgrade path" in r for r in reasons)

    def test_blockers_carried_through(self) -> None:
        g = AutonomyUpgradeGate(
            eval_pass_rate=0.99,
            red_team_pass_rate=1.0,
            successful_runs=100,
            blockers=["pending security review"],
        )
        ok, reasons = g.can_upgrade_to(AutonomyLevel.SEMI_AUTONOMOUS)
        assert not ok
        assert "pending security review" in reasons
