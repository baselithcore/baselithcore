"""Validate built distribution artifacts contain required plugin metadata."""

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
