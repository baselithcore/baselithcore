"""Prerelease ordering for :class:`core.plugins.version.SemanticVersion`.

Ordering used to be a plain string compare on the prerelease segment, which
puts ``beta.10`` *before* ``beta.2`` and therefore let a dependency constraint
accept a build older than the one it demanded. These tests pin the semver
rules: numeric identifiers compare numerically, a prerelease sorts below the
release it precedes, and build metadata is ignored.
"""

from itertools import pairwise

import pytest

from core.plugins.version import SemanticVersion, VersionConstraint


def _v(raw: str) -> SemanticVersion:
    return SemanticVersion(raw)


class TestPrereleaseOrdering:
    def test_numeric_prerelease_compares_numerically(self):
        assert _v("2.0.0-beta.2") < _v("2.0.0-beta.10")
        assert _v("2.0.0-beta.10") > _v("2.0.0-beta.2")

    def test_double_digit_rc_ordering(self):
        assert _v("1.0.0-rc.9") < _v("1.0.0-rc.11")

    def test_alpha_beta_rc_progression(self):
        ordered = [
            _v("1.0.0-alpha"),
            _v("1.0.0-alpha.1"),
            _v("1.0.0-beta"),
            _v("1.0.0-beta.2"),
            _v("1.0.0-rc.1"),
            _v("1.0.0"),
        ]
        for earlier, later in pairwise(ordered):
            assert earlier < later, f"{earlier} should sort below {later}"

    def test_prerelease_below_release(self):
        assert _v("1.0.0-alpha") < _v("1.0.0")
        assert _v("1.0.0") > _v("1.0.0-rc.1")

    def test_non_pep440_prerelease_uses_semver_rules(self):
        # ``alpha.beta`` is not a PEP 440 prerelease, so the fallback applies:
        # numeric identifiers rank below alphanumeric ones.
        assert _v("1.0.0-alpha.1") < _v("1.0.0-alpha.beta")

    def test_shorter_prerelease_sorts_first_when_prefix_equal(self):
        assert _v("1.0.0-alpha") < _v("1.0.0-alpha.1")

    def test_build_metadata_ignored(self):
        assert _v("1.0.0+build.1") == _v("1.0.0+build.2")
        assert not _v("1.0.0+build.1") < _v("1.0.0+build.2")

    def test_core_numbers_dominate_prerelease(self):
        assert _v("1.0.1-alpha") > _v("1.0.0")

    def test_equality_ignores_build(self):
        assert _v("2.3.4-rc.1+abc") == _v("2.3.4-rc.1+def")

    def test_sorting_a_mixed_list(self):
        raws = ["1.0.0", "1.0.0-beta.10", "1.0.0-beta.2", "0.9.9", "1.0.0-rc.1"]
        ordered = [str(v) for v in sorted(_v(r) for r in raws)]
        assert ordered == [
            "0.9.9",
            "1.0.0-beta.2",
            "1.0.0-beta.10",
            "1.0.0-rc.1",
            "1.0.0",
        ]


class TestConstraintsWithPrereleases:
    def test_ge_constraint_rejects_lower_numeric_prerelease(self):
        assert VersionConstraint(">=2.0.0-beta.10").satisfies("2.0.0-beta.2") is False

    def test_ge_constraint_accepts_higher_numeric_prerelease(self):
        assert VersionConstraint(">=2.0.0-beta.2").satisfies("2.0.0-beta.10") is True

    @pytest.mark.parametrize(
        ("constraint", "version", "expected"),
        [
            ("^1.2.3", "1.4.0", True),
            ("^1.2.3", "2.0.0", False),
            ("~1.2.3", "1.2.9", True),
            ("~1.2.3", "1.3.0", False),
            ("==1.0.0", "1.0.0", True),
            ("!=1.0.0", "1.0.1", True),
        ],
    )
    def test_existing_operators_unchanged(self, constraint, version, expected):
        assert VersionConstraint(constraint).satisfies(version) is expected
