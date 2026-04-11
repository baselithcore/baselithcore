"""Run mypy on official framework plugins.

This keeps typing pressure on first-party plugins without forcing the whole
`plugins/` tree to be type-clean in one step.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_PLUGIN_DIRS = (
    "plugins/api_routers",
    "plugins/browser_agent",
    "plugins/coding_agent",
    "plugins/document_sources",
    "plugins/web_scraper",
)


def collect_python_files() -> list[str]:
    """Return the Python files belonging to official plugins."""
    files: list[str] = []
    for relative_dir in OFFICIAL_PLUGIN_DIRS:
        plugin_dir = REPO_ROOT / relative_dir
        files.extend(
            path.relative_to(REPO_ROOT).as_posix()
            for path in sorted(plugin_dir.rglob("*.py"))
        )
    return files


def main() -> int:
    """CLI entrypoint."""
    files = collect_python_files()
    if not files:
        print("No official plugin Python files found.", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        "-m",
        "mypy",
        "--ignore-missing-imports",
        "--no-error-summary",
        *files,
    ]
    completed = subprocess.run(cmd, cwd=REPO_ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
