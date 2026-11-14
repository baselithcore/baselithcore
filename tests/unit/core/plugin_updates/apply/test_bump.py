"""What kind of step a release is, failing closed on anything unusual."""

from __future__ import annotations

import pytest

from baselith.core.plugin_updates.apply._bump import bump_kind


@pytest.mark.parametrize(
    ("installed", "latest", "kind"),
    [
        ("1.2.3", "1.2.4", "patch"),
        ("1.2.3", "1.2.10", "patch"),
        ("1.2.3", "1.3.0", "minor"),
        ("1.2.3", "2.0.0", "major"),
        ("1.2.3", "1.2.3", "same"),
        ("1.2.4", "1.2.3", "older"),
        ("1.3.0", "1.2.9", "older"),
        ("1.2.3", "1.2.4-rc1", "unparsable"),
        ("1.2.3-rc1", "1.2.3", "unparsable"),
        ("1.2", "1.2.1", "unparsable"),
        ("1.2.3", "1.2", "unparsable"),
        ("abc", "1.2.4", "unparsable"),
        ("1.2.3", "", "unparsable"),
        ("v1.2.3", "v1.2.4", "unparsable"),
        ("1.2.3+build.1", "1.2.4+build.2", "patch"),
    ],
)
def test_bump_kind_table(installed: str, latest: str, kind: str) -> None:
    assert bump_kind(installed, latest) == kind
