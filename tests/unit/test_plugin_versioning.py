"""Tests for plugin semantic versioning system."""

import pytest
from core.plugins.version import (
    SemanticVersion,
    VersionConstraint,
    check_version_compatibility,
    check_plugin_dependency,
)


class TestSemanticVersion:
    """Test SemanticVersion parsing and comparison."""

    def test_parse_valid_version(self):
        """Test parsing valid semantic versions."""
        v = SemanticVersion("1.2.3")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.prerelease is None
        assert v.build is None

    def test_parse_prerelease(self):
        """Test parsing version with prerelease."""
        v = SemanticVersion("1.2.3-beta.1")
        assert v.major == 1
        assert v.minor == 2
        assert v.patch == 3
        assert v.prerelease == "beta.1"

    def test_parse_build(self):
        """Test parsing version with build metadata."""
        v = SemanticVersion("1.2.3+20240101")
        assert v.major == 1
        assert v.build == "20240101"

    def test_parse_full(self):
        """Test parsing version with prerelease and build."""
        v = SemanticVersion("1.2.3-rc.1+build.123")
        assert v.prerelease == "rc.1"
        assert v.build == "build.123"

    def test_invalid_version(self):
        """Test that invalid versions raise ValueError."""
        with pytest.raises(ValueError, match="Invalid semantic version"):
            SemanticVersion("1.2")

        with pytest.raises(ValueError):
            SemanticVersion("v1.2.3")

        with pytest.raises(ValueError):
            SemanticVersion("1.2.3.4")

    def test_equality(self):
        """Test version equality (build ignored)."""
        v1 = SemanticVersion("1.2.3")
        v2 = SemanticVersion("1.2.3")
        v3 = SemanticVersion("1.2.3+build.1")
        v4 = SemanticVersion("1.2.3+build.2")

        assert v1 == v2
        assert v1 == v3  # Build metadata ignored
        assert v3 == v4  # Different builds still equal

    def test_comparison(self):
        """Test version comparison operators."""
        v1 = SemanticVersion("1.0.0")
        v2 = SemanticVersion("1.0.1")
        v3 = SemanticVersion("1.1.0")
        v4 = SemanticVersion("2.0.0")

        assert v1 < v2 < v3 < v4
        assert v4 > v3 > v2 > v1
        assert v1 <= v2
        assert v4 >= v3

    def test_prerelease_comparison(self):
        """Test that prerelease versions are less than release."""
        v_pre = SemanticVersion("1.0.0-beta")
        v_release = SemanticVersion("1.0.0")

        assert v_pre < v_release

    def test_str_representation(self):
        """Test string representation."""
        assert str(SemanticVersion("1.2.3")) == "1.2.3"
        assert str(SemanticVersion("1.2.3-beta")) == "1.2.3-beta"
        assert str(SemanticVersion("1.2.3+build")) == "1.2.3+build"
        assert str(SemanticVersion("1.2.3-rc+build")) == "1.2.3-rc+build"


class TestVersionConstraint:
    """Test VersionConstraint matching."""

    def test_exact_match(self):
        """Test exact version constraint."""
        c = VersionConstraint("1.2.3")
        assert c.satisfies("1.2.3")
        assert not c.satisfies("1.2.4")
        assert not c.satisfies("1.2.2")

    def test_greater_than(self):
        """Test greater than constraint."""
        c = VersionConstraint(">1.2.0")
        assert c.satisfies("1.2.1")
        assert c.satisfies("2.0.0")
        assert not c.satisfies("1.2.0")
        assert not c.satisfies("1.1.9")

    def test_greater_equal(self):
        """Test greater or equal constraint."""
        c = VersionConstraint(">=1.2.0")
        assert c.satisfies("1.2.0")
        assert c.satisfies("1.2.1")
        assert c.satisfies("2.0.0")
        assert not c.satisfies("1.1.9")

    def test_less_than(self):
        """Test less than constraint."""
        c = VersionConstraint("<2.0.0")
        assert c.satisfies("1.9.9")
        assert c.satisfies("1.0.0")
        assert not c.satisfies("2.0.0")
        assert not c.satisfies("2.0.1")

    def test_less_equal(self):
        """Test less or equal constraint."""
        c = VersionConstraint("<=2.0.0")
        assert c.satisfies("2.0.0")
        assert c.satisfies("1.9.9")
        assert not c.satisfies("2.0.1")

    def test_not_equal(self):
        """Test not equal constraint."""
        c = VersionConstraint("!=1.2.3")
        assert c.satisfies("1.2.4")
        assert c.satisfies("1.2.2")
        assert not c.satisfies("1.2.3")

    def test_caret_constraint(self):
        """Test caret (^) - compatible version constraint."""
        # For 1.x.x - allows minor and patch updates
        c = VersionConstraint("^1.2.3")
        assert c.satisfies("1.2.3")
        assert c.satisfies("1.2.4")  # Patch OK
        assert c.satisfies("1.9.0")  # Minor OK
        assert not c.satisfies("2.0.0")  # Major change not OK
        assert not c.satisfies("1.2.2")  # Lower not OK

    def test_caret_constraint_zero_major(self):
        """Test caret constraint with 0.x versions."""
        # For 0.x.x - only allows patch updates (minor is breaking)
        c = VersionConstraint("^0.2.3")
        assert c.satisfies("0.2.3")
        assert c.satisfies("0.2.4")  # Patch OK
        assert not c.satisfies("0.3.0")  # Minor change breaks
        assert not c.satisfies("1.0.0")  # Major change breaks

    def test_tilde_constraint(self):
        """Test tilde (~) - approximately equivalent constraint."""
        # Allows patch updates only
        c = VersionConstraint("~1.2.3")
        assert c.satisfies("1.2.3")
        assert c.satisfies("1.2.4")
        assert c.satisfies("1.2.9")
        assert not c.satisfies("1.3.0")  # Minor change not OK
        assert not c.satisfies("1.2.2")  # Lower not OK

    def test_constraint_str(self):
        """Test string representation of constraints."""
        assert str(VersionConstraint("^1.2.3")) == "^1.2.3"
        assert str(VersionConstraint(">=1.0.0")) == ">=1.0.0"


class TestVersionCompatibility:
    """Test version compatibility checking."""

    def test_check_within_bounds(self):
        """Test version is within min/max bounds."""
        assert check_version_compatibility(
            "1.5.0", min_version="1.0.0", max_version="2.0.0"
        )
        assert check_version_compatibility(
            "1.0.0", min_version="1.0.0", max_version="2.0.0"
        )
        assert check_version_compatibility(
            "2.0.0", min_version="1.0.0", max_version="2.0.0"
        )

    def test_check_below_min(self):
        """Test version below minimum."""
        assert not check_version_compatibility("0.9.0", min_version="1.0.0")

    def test_check_above_max(self):
        """Test version above maximum."""
        assert not check_version_compatibility("3.0.0", max_version="2.0.0")

    def test_check_only_min(self):
        """Test with only minimum constraint."""
        assert check_version_compatibility("2.0.0", min_version="1.0.0")
        assert not check_version_compatibility("0.9.0", min_version="1.0.0")

    def test_check_only_max(self):
        """Test with only maximum constraint."""
        assert check_version_compatibility("1.0.0", max_version="2.0.0")
        assert not check_version_compatibility("3.0.0", max_version="2.0.0")

    def test_invalid_version_returns_false(self):
        """Test that invalid versions return False."""
        assert not check_version_compatibility("invalid", min_version="1.0.0")


class TestPluginDependency:
    """Test plugin dependency checking."""

    def test_dependency_satisfied(self):
        """Test satisfied dependencies."""
        assert check_plugin_dependency("1.5.0", "^1.2.0")
        assert check_plugin_dependency("2.1.0", ">=2.0.0")
        assert check_plugin_dependency("1.2.5", "~1.2.0")

    def test_dependency_not_satisfied(self):
        """Test unsatisfied dependencies."""
        assert not check_plugin_dependency("2.0.0", "^1.2.0")
        assert not check_plugin_dependency("1.9.0", ">=2.0.0")
        assert not check_plugin_dependency("1.3.0", "~1.2.0")

    def test_invalid_dependency_returns_false(self):
        """Test invalid inputs return False."""
        assert not check_plugin_dependency("invalid", "^1.0.0")
        # Invalid constraint should also be handled gracefully
        # (current implementation may raise, could be improved)


@pytest.mark.parametrize(
    "version,constraint,expected",
    [
        ("1.0.0", "^1.0.0", True),
        ("1.5.0", "^1.0.0", True),
        ("2.0.0", "^1.0.0", False),
        ("0.2.5", "^0.2.0", True),
        ("0.3.0", "^0.2.0", False),
        ("1.2.9", "~1.2.0", True),
        ("1.3.0", "~1.2.0", False),
        ("2.0.0", ">=1.5.0", True),
        ("1.4.9", ">=1.5.0", False),
        ("1.2.3", "1.2.3", True),
        ("1.2.4", "1.2.3", False),
    ],
)
def test_version_constraint_combinations(version, constraint, expected):
    """Parametric test for various version/constraint combinations."""
    c = VersionConstraint(constraint)
    assert c.satisfies(version) == expected
