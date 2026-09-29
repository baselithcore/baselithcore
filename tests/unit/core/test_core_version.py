"""``core/_core_version.py``: the public core release this tree corresponds to.

The file is byte-identical in every distribution. Where ``core/_version.py``
is the public core's own version (no ``__distribution__`` marker) the two must
agree, and the release job must rewrite both. A downstream distribution marks
its ``_version.py`` with ``__distribution__``: there the two version
independently and its release job must never rewrite ``_core_version.py``
(the file arrives through the core alignment from the public project).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import core._version as version_module
from core._core_version import CORE_VERSION

ROOT = Path(__file__).resolve().parents[3]
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_WRITES_CORE_VERSION = re.compile(r"""Path\(\s*['"]core/_core_version\.py['"]\s*\)""")
_DISTRIBUTION: str | None = getattr(version_module, "__distribution__", None)


def _prepare_cmd() -> str:
    """The semantic-release ``prepareCmd`` of this checkout's ``.releaserc``."""
    config = json.loads((ROOT / ".releaserc").read_text(encoding="utf-8"))
    for plugin in config["plugins"]:
        if isinstance(plugin, list) and plugin[0] == "@semantic-release/exec":
            return str(plugin[1]["prepareCmd"])
    raise AssertionError(".releaserc has no @semantic-release/exec prepareCmd")


def test_core_version_is_a_plain_release_semver() -> None:
    assert _SEMVER.match(CORE_VERSION), CORE_VERSION


def test_framework_version_is_a_plain_release_semver() -> None:
    assert _SEMVER.match(version_module.__version__), version_module.__version__


@pytest.mark.skipif(
    _DISTRIBUTION is not None,
    reason="a downstream distribution versions independently of the core",
)
def test_public_core_versions_agree() -> None:
    assert CORE_VERSION == version_module.__version__, (
        "core/_core_version.py and core/_version.py must name the same release "
        "in the public core project; bump both together"
    )


@pytest.mark.skipif(not (ROOT / ".releaserc").is_file(), reason="no .releaserc")
def test_release_job_owns_core_version_only_in_the_public_core() -> None:
    writes_core_version = bool(_WRITES_CORE_VERSION.search(_prepare_cmd()))
    if _DISTRIBUTION is None:
        assert writes_core_version, (
            "the public core's release job must rewrite core/_core_version.py "
            "together with core/_version.py"
        )
    else:
        assert not writes_core_version, (
            "a distribution's release job must not rewrite core/_core_version.py: "
            "it is the public core release and comes from the core alignment"
        )
