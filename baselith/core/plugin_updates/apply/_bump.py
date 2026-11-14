"""What kind of step one version to another is, for auto-apply (fails closed).

Unlike the checker's ``_is_newer``, which treats a version it cannot parse as
newer so the release is still announced, this answers ``unparsable`` for
anything that is not a plain ``MAJOR.MINOR.PATCH`` on both sides: a pre-release
on either side, a missing part, a ``v`` prefix. Build metadata is ignored, as
semver orders it. Auto-apply installs only ``patch``.
"""

from __future__ import annotations

from typing import Literal

from baselith.core.plugins.version import SemanticVersion

BumpKind = Literal["patch", "minor", "major", "same", "older", "unparsable"]


def bump_kind(installed: str, latest: str) -> BumpKind:
    """The step from ``installed`` to ``latest``; ``unparsable`` when unsure."""
    try:
        old, new = SemanticVersion(installed), SemanticVersion(latest)
    except (ValueError, TypeError):
        return "unparsable"
    if old.prerelease or new.prerelease:
        return "unparsable"
    if new == old:
        return "same"
    if new < old:
        return "older"
    if new.major != old.major:
        return "major"
    if new.minor != old.minor:
        return "minor"
    return "patch"


__all__ = ["BumpKind", "bump_kind"]
