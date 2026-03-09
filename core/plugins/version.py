"""Semantic versioning utilities for plugin dependency management."""

import re
from typing import Optional, Tuple
from core.observability.logging import get_logger

logger = get_logger(__name__)


class SemanticVersion:
    """
    Semantic version parser and comparator.

    Supports semver format: MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]
    """

    def __init__(self, version: str):
        """
        Parse a semantic version string.

        Args:
            version: Version string (e.g., "1.2.3", "2.0.0-beta.1")

        Raises:
            ValueError: If version string is invalid
        """
        self.raw = version
        self.major, self.minor, self.patch, self.prerelease, self.build = self._parse(
            version
        )

    def _parse(
        self, version: str
    ) -> Tuple[int, int, int, Optional[str], Optional[str]]:
        """Parse version string into components."""
        # Pattern: MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]
        pattern = (
            r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z\-.]+))?(?:\+([0-9A-Za-z\-.]+))?$"
        )
        match = re.match(pattern, version)

        if not match:
            raise ValueError(f"Invalid semantic version: {version}")

        major, minor, patch, prerelease, build = match.groups()
        return int(major), int(minor), int(patch), prerelease, build

    def __eq__(self, other: object) -> bool:
        """Check equality (ignores build metadata)."""
        if not isinstance(other, SemanticVersion):
            return False
        return (
            self.major == other.major
            and self.minor == other.minor
            and self.patch == other.patch
            and self.prerelease == other.prerelease
        )

    def __lt__(self, other: "SemanticVersion") -> bool:
        """Check if this version is less than another."""
        # Compare major.minor.patch
        if (self.major, self.minor, self.patch) != (
            other.major,
            other.minor,
            other.patch,
        ):
            return (self.major, self.minor, self.patch) < (
                other.major,
                other.minor,
                other.patch,
            )

        # If versions are equal, check prerelease
        # Version with prerelease < version without prerelease
        if self.prerelease and not other.prerelease:
            return True
        if not self.prerelease and other.prerelease:
            return False
        if self.prerelease and other.prerelease:
            return self.prerelease < other.prerelease

        return False

    def __le__(self, other: "SemanticVersion") -> bool:
        return self < other or self == other

    def __gt__(self, other: "SemanticVersion") -> bool:
        return not self <= other

    def __ge__(self, other: "SemanticVersion") -> bool:
        return not self < other

    def __str__(self) -> str:
        result = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            result += f"-{self.prerelease}"
        if self.build:
            result += f"+{self.build}"
        return result

    def __repr__(self) -> str:
        return f"SemanticVersion('{self}')"


class VersionConstraint:
    """
    Version constraint checker.

    Supports operators: ==, !=, >, >=, <, <=, ^, ~
    Examples:
        - ">=1.2.0" - Greater than or equal to 1.2.0
        - "^1.2.3" - Compatible with 1.2.3 (>=1.2.3, <2.0.0)
        - "~1.2.3" - Approximately 1.2.3 (>=1.2.3, <1.3.0)
    """

    def __init__(self, constraint: str):
        """
        Parse a version constraint.

        Args:
            constraint: Constraint string (e.g., ">=1.2.0", "^2.1.0")
        """
        self.raw = constraint.strip()
        self.operator, self.version = self._parse_constraint(self.raw)

    def _parse_constraint(self, constraint: str) -> Tuple[str, SemanticVersion]:
        """Parse constraint into operator and version."""
        # Caret (^) - compatible version
        if constraint.startswith("^"):
            return "^", SemanticVersion(constraint[1:])

        # Tilde (~) - approximately equivalent
        if constraint.startswith("~"):
            return "~", SemanticVersion(constraint[1:])

        # Comparison operators
        for op in [">=", "<=", "==", "!=", ">", "<"]:
            if constraint.startswith(op):
                return op, SemanticVersion(constraint[len(op) :].strip())

        # No operator = exact match
        return "==", SemanticVersion(constraint)

    def satisfies(self, version: str) -> bool:
        """
        Check if a version satisfies this constraint.

        Args:
            version: Version string to check

        Returns:
            True if version satisfies constraint
        """
        try:
            v = SemanticVersion(version)
        except ValueError as e:
            logger.warning(f"Invalid version '{version}': {e}")
            return False

        if self.operator == "==":
            return v == self.version
        elif self.operator == "!=":
            return v != self.version
        elif self.operator == ">":
            return v > self.version
        elif self.operator == ">=":
            return v >= self.version
        elif self.operator == "<":
            return v < self.version
        elif self.operator == "<=":
            return v <= self.version
        elif self.operator == "^":
            # Compatible: same major version (or 0.minor for 0.x)
            if self.version.major == 0:
                # 0.x.y - minor version must match
                return (
                    v.major == 0 and v.minor == self.version.minor and v >= self.version
                )
            else:
                # x.y.z - major version must match
                return v.major == self.version.major and v >= self.version
        elif self.operator == "~":
            # Approximately: same major.minor version
            return (
                v.major == self.version.major
                and v.minor == self.version.minor
                and v >= self.version
            )

        return False

    def __str__(self) -> str:
        return f"{self.operator}{self.version}"

    def __repr__(self) -> str:
        return f"VersionConstraint('{self.raw}')"


def check_version_compatibility(
    version: str,
    min_version: Optional[str] = None,
    max_version: Optional[str] = None,
) -> bool:
    """
    Check if a version is within min/max bounds.

    Args:
        version: Version to check
        min_version: Minimum acceptable version (inclusive)
        max_version: Maximum acceptable version (inclusive)

    Returns:
        True if version is compatible
    """
    try:
        v = SemanticVersion(version)

        if min_version:
            min_v = SemanticVersion(min_version)
            if v < min_v:
                return False

        if max_version:
            max_v = SemanticVersion(max_version)
            if v > max_v:
                return False

        return True

    except ValueError as e:
        logger.error(f"Version compatibility check failed: {e}")
        return False


def check_plugin_dependency(available_version: str, required_constraint: str) -> bool:
    """
    Check if an available plugin version satisfies a dependency constraint.

    Args:
        available_version: Version of available plugin
        required_constraint: Required version constraint

    Returns:
        True if dependency is satisfied
    """
    try:
        constraint = VersionConstraint(required_constraint)
        return constraint.satisfies(available_version)
    except Exception as e:
        logger.error(
            f"Dependency check failed for {available_version} vs {required_constraint}: {e}"
        )
        return False
