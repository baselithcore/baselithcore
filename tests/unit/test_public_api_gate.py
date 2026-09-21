"""Tests for the public API surface drift gate (scripts/check_public_api.py)."""

from __future__ import annotations

from pathlib import Path

from core.stability import PACKAGE_STABILITY, Stability
from scripts.check_public_api import (
    BASELINE_PATH,
    REPO_ROOT,
    check_public_api,
    check_stability,
    check_stability_table,
    check_tier_ratchet,
    collect_public_api,
    diff_public_api,
    dynamic_packages,
    load_baseline,
    load_baseline_tiers,
    read_literal_all,
    top_level_packages,
    write_baseline,
)


def _package(root: Path, dotted: str, body: str) -> None:
    directory = root.joinpath(*dotted.split("."))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "__init__.py").write_text(body, encoding="utf-8")


def _tree(root: Path) -> None:
    _package(root, "core", '__all__ = ["__version__"]\n')
    _package(root, "core.alpha", 'from .a import A, B\n\n__all__ = ("B", "A")\n')
    _package(root, "core.beta", "__all__ = [\n    'run',\n]\n")
    _package(
        root, "core.dyn", "__all__ = [n for n in dir() if not n.startswith('_')]\n"
    )
    _package(root, "core.noall", "x = 1\n")


def test_collects_sorted_literal_alls_only(tmp_path: Path) -> None:
    _tree(tmp_path)

    assert collect_public_api(tmp_path) == {
        "core": ["__version__"],
        "core.alpha": ["A", "B"],
        "core.beta": ["run"],
    }
    assert dynamic_packages(tmp_path) == ["core.dyn", "core.noall"]


def test_augmented_all_is_treated_as_dynamic(tmp_path: Path) -> None:
    init = tmp_path / "__init__.py"
    init.write_text('__all__ = ["a"]\n__all__ += ["b"]\n', encoding="utf-8")

    assert read_literal_all(init) is None


def test_removed_symbol_is_reported_with_its_release_cost(tmp_path: Path) -> None:
    """The message states what the removal costs, not just that it happened."""
    _tree(tmp_path)
    baseline = {
        "core": ["__version__"],
        "core.alpha": ["A", "B", "C"],
        "core.beta": ["run"],
    }

    violations = check_public_api(tmp_path, baseline=baseline)

    assert len(violations) == 1
    assert violations[0].startswith("core.alpha no longer exports C")
    # An unclassified package falls back to the weakest promise.
    assert "MINOR" in violations[0]


def test_removal_cost_follows_the_package_tier(tmp_path: Path) -> None:
    """A stable package and an experimental one do not cost the same to change."""
    _package(tmp_path, "core.agent", '__all__ = ["Agent"]\n')
    _package(tmp_path, "core.swarm", '__all__ = ["Auction"]\n')
    baseline = {
        "core.agent": ["Agent", "Retired"],
        "core.swarm": ["Auction", "Retired"],
    }

    by_package = {
        violation.split(" ")[0]: violation
        for violation in check_public_api(tmp_path, baseline=baseline)
    }

    assert "BREAKING (MAJOR)" in by_package["core.agent"]
    assert "BREAKING" not in by_package["core.swarm"]
    assert "experimental" in by_package["core.swarm"]


def test_added_symbol_requires_baseline_refresh(tmp_path: Path) -> None:
    _tree(tmp_path)
    baseline = {"core": ["__version__"], "core.alpha": ["A"], "core.beta": ["run"]}

    violations = check_public_api(tmp_path, baseline=baseline)

    assert len(violations) == 1
    assert "core.alpha newly exports B" in violations[0]
    assert "--update-baseline" in violations[0]


def test_vanished_package_removes_every_symbol(tmp_path: Path) -> None:
    _tree(tmp_path)
    baseline = {**collect_public_api(tmp_path), "core.gone": ["x", "y"]}

    diff = diff_public_api(baseline, collect_public_api(tmp_path))

    assert diff.removed == {"core.gone": ["x", "y"]}
    assert diff.added == {}


def test_baseline_roundtrip(tmp_path: Path) -> None:
    _tree(tmp_path)
    baseline_path = tmp_path / "baseline.json"

    assert write_baseline(tmp_path, baseline_path) == 4
    assert load_baseline(baseline_path) == collect_public_api(tmp_path)
    assert check_public_api(tmp_path, baseline=load_baseline(baseline_path)) == []


def test_repo_surface_matches_committed_baseline() -> None:
    violations = check_public_api(REPO_ROOT, baseline=load_baseline(BASELINE_PATH))

    assert violations == [], "\n".join(violations)


class TestStabilityTable:
    """The tier table must keep describing the tree it classifies."""

    def test_classifies_exactly_the_packages_that_exist(self) -> None:
        """No package arrives unclassified, no entry outlives its package."""
        assert set(PACKAGE_STABILITY) == top_level_packages(REPO_ROOT)

    def test_an_unclassified_package_is_a_violation(self, tmp_path: Path) -> None:
        _package(tmp_path, "core.brandnew", '__all__ = ["thing"]\n')

        violations = check_stability_table(tmp_path)

        assert any("core.brandnew has no stability tier" in v for v in violations)

    def test_an_entry_without_a_package_is_a_violation(self, tmp_path: Path) -> None:
        """The table cannot describe packages that were deleted."""
        _package(tmp_path, "baselith", '__all__ = ["Agent"]\n')

        violations = check_stability_table(tmp_path)

        assert any("core.agent" in v and "no longer exists" in v for v in violations)


class TestTierRatchet:
    """A promise may get stronger, or be retired. It may not get weaker."""

    def test_unchanged_tiers_pass(self) -> None:
        recorded = {p: str(t) for p, t in PACKAGE_STABILITY.items()}

        assert check_tier_ratchet(recorded) == []

    def test_demotion_is_rejected(self) -> None:
        """core.agent ships as stable; calling it beta withdraws that."""
        recorded = {p: str(t) for p, t in PACKAGE_STABILITY.items()}
        recorded["core.swarm"] = str(Stability.STABLE)

        violations = check_tier_ratchet(recorded)

        assert len(violations) == 1
        assert (
            "core.swarm was published as 'stable' and is now 'experimental'"
            in (violations[0])
        )

    def test_promotion_is_allowed(self) -> None:
        recorded = {p: str(t) for p, t in PACKAGE_STABILITY.items()}
        recorded["core.agent"] = str(Stability.EXPERIMENTAL)

        assert check_tier_ratchet(recorded) == []

    def test_retirement_is_allowed_from_any_tier(self) -> None:
        """Announcing a retirement is never itself a regression."""
        recorded = {"core.agents": str(Stability.STABLE)}

        assert check_tier_ratchet(recorded) == []

    def test_an_unknown_recorded_tier_is_ignored(self) -> None:
        """A baseline written by an older version must not crash the gate."""
        assert check_tier_ratchet({"core.agent": "gold-plated"}) == []


def test_repo_stability_matches_committed_baseline() -> None:
    violations = check_stability(
        REPO_ROOT, baseline_tiers=load_baseline_tiers(BASELINE_PATH)
    )

    assert violations == [], "\n".join(violations)
