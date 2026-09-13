"""Validate built distribution artifacts contain required plugin metadata.

Two directions, because a distribution can be wrong in both:

* every official plugin must arrive with the manifest and README the loader
  and the marketplace read, and
* the in-repo FIXTURE plugins must not arrive at all. ``plugins/example-plugin``
  (the authoring-guide scaffold) and ``plugins/test-project`` (the tree the
  plugin CLI tests scaffold against) were swept into the wheel by
  ``include = ["plugins*"]``, so every install carried a loadable plugin that
  exists only to be asserted on. ``[tool.setuptools.packages.find] exclude``
  keeps them out; this asserts it on the built artifact, because a pattern that
  silently stops matching is how they shipped in the first place.
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
PLUGIN_FILES = (
    "plugins/api_routers/manifest.yaml",
    "plugins/api_routers/README.md",
    "plugins/browser_agent/manifest.yaml",
    "plugins/browser_agent/README.md",
    "plugins/coding_agent/manifest.yaml",
    "plugins/coding_agent/README.md",
    "plugins/document_sources/manifest.yaml",
    "plugins/document_sources/README.md",
    "plugins/web_scraper/manifest.yaml",
    "plugins/web_scraper/README.md",
)
# Wheel-only: the sdist is the source archive and may legitimately carry the
# fixtures (installing from it re-runs the backend, which applies the same
# `exclude`). The wheel is what `pip install baselith-core` unpacks verbatim.
FORBIDDEN_WHEEL_PREFIXES = (
    "plugins/example-plugin/",
    "plugins/test-project/",
)
# Source maps. The baselithbot dashboard builds with `sourcemap: 'hidden'`,
# which still writes .map files — they are for uploading to an error tracker,
# not for shipping. In the wheel they would roughly triple the packaged
# dashboard and expose its original TypeScript sources, and because `hidden`
# strips the sourceMappingURL comment nothing would ever even request them.
FORBIDDEN_WHEEL_SUFFIXES = (".map",)


def _sdist_has_members(path: Path) -> list[str]:
    missing: list[str] = []
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
        for plugin_file in PLUGIN_FILES:
            if not any(name.endswith(plugin_file) for name in names):
                missing.append(plugin_file)
    return missing


def _wheel_has_members(path: Path) -> list[str]:
    missing: list[str] = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        for plugin_file in PLUGIN_FILES:
            if plugin_file not in names:
                missing.append(plugin_file)
    return missing


def _preview(names: list[str], limit: int = 5) -> str:
    """Render at most ``limit`` names, noting how many were elided."""
    head = ", ".join(names[:limit])
    if len(names) <= limit:
        return head
    return f"{head} (+{len(names) - limit} more)"


def _wheel_has_fixtures(path: Path) -> list[str]:
    """Return wheel members that belong to an in-repo fixture plugin."""
    with zipfile.ZipFile(path) as archive:
        return [
            name
            for name in archive.namelist()
            if name.startswith(FORBIDDEN_WHEEL_PREFIXES)
        ]


def _wheel_has_source_maps(path: Path) -> list[str]:
    """Return wheel members that are build source maps."""
    with zipfile.ZipFile(path) as archive:
        return [
            name
            for name in archive.namelist()
            if name.endswith(FORBIDDEN_WHEEL_SUFFIXES)
        ]


def main() -> int:
    """CLI entrypoint."""
    wheel_files = sorted(DIST_DIR.glob("*.whl"))
    sdist_files = sorted(DIST_DIR.glob("*.tar.gz"))

    if not wheel_files or not sdist_files:
        print("Both wheel and sdist artifacts must exist in dist/.", file=sys.stderr)
        return 1

    violations: list[str] = []

    for wheel in wheel_files:
        missing = _wheel_has_members(wheel)
        if missing:
            violations.append(f"{wheel.name}: missing {', '.join(missing)}")
        shipped_fixtures = _wheel_has_fixtures(wheel)
        if shipped_fixtures:
            violations.append(
                f"{wheel.name}: ships in-repo fixture plugins: "
                f"{_preview(shipped_fixtures)}. "
                "Restore the `exclude` patterns under "
                "[tool.setuptools.packages.find] in pyproject.toml. If those "
                "patterns are present and this still fails, the build reused "
                "stale local artifacts: `build/` keeps files that are no longer "
                "part of any package, and a stale `*.egg-info/SOURCES.txt` "
                "re-injects them because pyproject-based setuptools defaults "
                "include-package-data to true. Delete both and rebuild "
                "(CI checks out fresh, so it never sees this)."
            )
        source_maps = _wheel_has_source_maps(wheel)
        if source_maps:
            violations.append(
                f"{wheel.name}: ships source maps: {_preview(source_maps)}. "
                "The UI builds with sourcemap: 'hidden', so nothing requests "
                "them — they exist to be uploaded to an error tracker, not "
                "shipped. Restore the `ui/dist/**/*.map` entry under "
                "[tool.setuptools.exclude-package-data] in pyproject.toml."
            )

    for sdist in sdist_files:
        missing = _sdist_has_members(sdist)
        if missing:
            violations.append(f"{sdist.name}: missing {', '.join(missing)}")

    if violations:
        print("Distribution artifact validation failed:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    print("Distribution artifacts OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
