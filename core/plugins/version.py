"""Semantic versioning utilities for plugin dependency management."""

import os
import re

from packaging.version import InvalidVersion
from packaging.version import Version as PackagingVersion

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: A prerelease identifier made only of digits (semver: compares numerically).
_NUMERIC_IDENTIFIER = re.compile(r"^\d+$")


def _pep440_prerelease(prerelease: str) -> PackagingVersion | None:
    """Parse a semver prerelease as a PEP 440 prerelease, or return ``None``.

    ``packaging.version.Version`` is the reference implementation for ordering
    the prerelease shapes people actually write (``beta.2`` vs ``beta.10``,
    ``alpha`` < ``beta`` < ``rc``), which a string compare gets wrong. It is
    only trusted when it really read the segment *as a prerelease*: PEP 440
    also parses ``-1`` as an implicit post-release, whose ordering contradicts
    semver, so anything carrying a post/dev/local component is rejected here
    and handled by the semver fallback instead.

    Args:
        prerelease: The prerelease segment, without the leading ``-``.

    Returns:
        The parsed version, or ``None`` when PEP 440 cannot express it.
    """
    try:
        parsed = PackagingVersion(f"0.0.0-{prerelease}")
    except InvalidVersion:
        return None
    if parsed.pre is None or parsed.post is not None or parsed.dev is not None:
        return None
    if parsed.local is not None:
        return None
    return parsed


def _semver_prerelease_key(prerelease: str) -> tuple[tuple[int, int, str], ...]:
    """Build the semver §11 precedence key for a prerelease segment.

    Identifiers are compared field by field: numeric ones numerically, and
    always below alphanumeric ones; a shorter prefix sorts below the longer
    string that extends it.
    """
    key: list[tuple[int, int, str]] = []
    for identifier in prerelease.split("."):
        if _NUMERIC_IDENTIFIER.match(identifier):
            key.append((0, int(identifier), ""))
        else:
            key.append((1, 0, identifier))
    return tuple(key)


def _compare_prerelease(left: str, right: str) -> int:
    """Order two non-empty prerelease segments (-1, 0 or 1)."""
    left_pep440 = _pep440_prerelease(left)
    right_pep440 = _pep440_prerelease(right)
    if left_pep440 is not None and right_pep440 is not None:
        return int(left_pep440 > right_pep440) - int(left_pep440 < right_pep440)

    left_key = _semver_prerelease_key(left)
    right_key = _semver_prerelease_key(right)
    return (left_key > right_key) - (left_key < right_key)


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

    def _parse(self, version: str) -> tuple[int, int, int, str | None, str | None]:
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

    def _compare(self, other: "SemanticVersion") -> int:
        """Total order over two versions (-1, 0 or 1), ignoring build metadata.

        Build metadata is excluded from precedence by the semver spec, so
        ``1.0.0+a`` and ``1.0.0+b`` compare equal.
        """
        mine = (self.major, self.minor, self.patch)
        theirs = (other.major, other.minor, other.patch)
        if mine != theirs:
            return -1 if mine < theirs else 1

        if self.prerelease == other.prerelease:
            return 0
        # A prerelease always precedes the release it leads up to.
        if self.prerelease and not other.prerelease:
            return -1
        if not self.prerelease and other.prerelease:
            return 1
        return _compare_prerelease(self.prerelease or "", other.prerelease or "")

    def __eq__(self, other: object) -> bool:
        """Check equality (ignores build metadata)."""
        if not isinstance(other, SemanticVersion):
            return False
        return self._compare(other) == 0

    def __lt__(self, other: "SemanticVersion") -> bool:
        """Check if this version is less than another."""
        return self._compare(other) < 0

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

    def _parse_constraint(self, constraint: str) -> tuple[str, SemanticVersion]:
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
    min_version: str | None = None,
    max_version: str | None = None,
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


#: Values that explicitly *downgrade* a fail-closed gate to warn-only.
_FALSEY = ("0", "false", "no", "off")


def is_compat_enforcement_enabled() -> bool:
    """Whether to refuse loading version-incompatible plugins.

    **Fail-closed.** A plugin whose declared core-version bounds or plugin
    dependencies are not satisfied is skipped, because the declaration is the
    plugin author's statement about what it is safe to run against — running
    it anyway and hoping was never a safe default. Set
    ``BASELITH_ENFORCE_PLUGIN_COMPAT=false`` (or ``0``/``no``/``off``) as an
    explicit, temporary downgrade to restore the old warn-only behaviour while
    a manifest's bounds are corrected.

    Returns:
        True unless the environment explicitly disables enforcement.
    """
    raw = os.environ.get("BASELITH_ENFORCE_PLUGIN_COMPAT", "").strip().lower()
    return raw not in _FALSEY


def check_plugin_compatibility(
    *,
    core_version: str,
    min_core_version: str | None = None,
    max_core_version: str | None = None,
    plugin_dependencies: dict[str, str] | None = None,
    available_versions: dict[str, str] | None = None,
) -> list[str]:
    """Collect compatibility problems for a plugin against the running system.

    Checks the plugin's declared core-version bounds against ``core_version``
    and each versioned entry in ``plugin_dependencies`` against the versions of
    plugins actually available (``available_versions``). Pure inspection — never
    raises; the caller decides whether to warn or skip based on
    :func:`is_compat_enforcement_enabled`.

    Args:
        core_version: Version of the running core framework.
        min_core_version: Minimum core version the plugin declares, if any.
        max_core_version: Maximum core version the plugin declares, if any.
        plugin_dependencies: Map of dependency plugin name -> version constraint.
        available_versions: Map of available plugin name -> version.

    Returns:
        A list of human-readable problem strings; empty when fully compatible.
    """
    problems: list[str] = []
    available = available_versions or {}

    if min_core_version or max_core_version:
        if not check_version_compatibility(
            core_version, min_core_version, max_core_version
        ):
            bounds = f"{min_core_version or '*'}..{max_core_version or '*'}"
            problems.append(
                f"requires core version {bounds}, but running core {core_version}"
            )

    for dep_name, constraint in (plugin_dependencies or {}).items():
        dep_version = available.get(dep_name)
        if dep_version is None:
            problems.append(
                f"missing plugin dependency '{dep_name}' (requires {constraint})"
            )
        elif not check_plugin_dependency(dep_version, constraint):
            problems.append(
                f"plugin dependency '{dep_name}' v{dep_version} "
                f"does not satisfy '{constraint}'"
            )

    return problems
