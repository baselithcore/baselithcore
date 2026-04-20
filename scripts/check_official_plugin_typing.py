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
    "plugins/baselithbot",
    "plugins/browser_agent",
    "plugins/coding_agent",
    "plugins/document_sources",
    "plugins/web_scraper",
)

# Subdirectories within OFFICIAL_PLUGIN_DIRS that are excluded from the strict
# typing gate (non-Python assets, generated code, or opt-in modules with
# separately managed typing).
EXCLUDED_SUBPATHS: tuple[str, ...] = (
    "plugins/baselithbot/ui",
    "plugins/baselithbot/dashboard",
    "plugins/baselithbot/docs",
    "plugins/baselithbot/.state",
    "plugins/baselithbot/tests",
)


def _is_excluded(relative_path: str) -> bool:
    return any(relative_path.startswith(prefix) for prefix in EXCLUDED_SUBPATHS)


def collect_python_files() -> list[str]:
    """Return the Python files belonging to official plugins."""
    files: list[str] = []
    for relative_dir in OFFICIAL_PLUGIN_DIRS:
        plugin_dir = REPO_ROOT / relative_dir
        for path in sorted(plugin_dir.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if _is_excluded(rel):
                continue
            files.append(rel)
    return files


def main() -> int:
    """CLI entrypoint."""
    files = collect_python_files()
    if not files:
        print("No official plugin Python files found.", file=sys.stderr)
        return 1

    # NOTE: ``--warn-unused-ignores`` is intentionally omitted. Under
    # ``--ignore-missing-imports`` + ``--follow-imports=skip`` mypy cannot
    # distinguish a legit optional-dep ignore from a stale one, so the two
    # flags together generate false-positives on plugins that legitimately
    # guard optional imports (e.g. playwright_stealth, psutil, prometheus).
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        "--ignore-missing-imports",
        "--follow-imports=skip",
        "--no-error-summary",
        *files,
    ]
    completed = subprocess.run(cmd, cwd=REPO_ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
