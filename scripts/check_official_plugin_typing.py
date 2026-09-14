"""Run mypy on official framework plugins.

This keeps typing pressure on first-party plugins without forcing the whole
`plugins/` tree to be type-clean in one step.

The roster is **derived, not hardcoded**: an official plugin is a directory
under `plugins/` whose manifest declares `integrity_sha256`, i.e. one this repo
signs and ships. The old hardcoded tuple silently dropped `plugins/goals`,
`plugins/reasoning_agent` and `plugins/baselithbot/dashboard` — a plugin added
after the list was written was simply never gated.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mypy_runner import MypyNotFoundError, mypy_base_command

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGINS_ROOT = REPO_ROOT / "plugins"

MANIFEST_FILENAMES = ("manifest.yaml", "manifest.yml", "manifest.json")

#: Manifest-bearing directories that are deliberately unsigned, and therefore
#: deliberately outside the gate: ``example-plugin`` is the authoring reference
#: (and its hyphenated name is not an importable package anyway), and
#: ``test-project`` is scaffolding fixture, not a shipped plugin. Anything else
#: without an ``integrity_sha256`` is a mistake — see :func:`official_plugin_dirs`.
UNSIGNED_BY_DESIGN: frozenset[str] = frozenset({"example-plugin", "test-project"})

# Subdirectories within the official plugins that are excluded from the strict
# typing gate (non-Python assets, generated code, vendored trees, or test
# suites with their own conventions).
EXCLUDED_SUBPATHS: tuple[str, ...] = (
    "plugins/baselithbot/ui",
    "plugins/baselithbot/docs",
    "plugins/baselithbot/.state",
    "plugins/baselithbot/tests",
)


def _is_excluded(relative_path: str) -> bool:
    return any(relative_path.startswith(prefix) for prefix in EXCLUDED_SUBPATHS)


def _manifest_declares_integrity(plugin_dir: Path) -> bool:
    """Whether the plugin's manifest carries an ``integrity_sha256`` field."""
    for filename in MANIFEST_FILENAMES:
        manifest = plugin_dir / filename
        if not manifest.exists():
            continue
        try:
            if manifest.suffix == ".json":
                data = json.loads(manifest.read_text(encoding="utf-8"))
            else:
                import yaml

                data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        except Exception as exc:  # unreadable manifest is not an official plugin
            print(f"warning: could not read {manifest}: {exc}", file=sys.stderr)
            return False
        return bool(isinstance(data, dict) and data.get("integrity_sha256"))
    return False


def _has_manifest(plugin_dir: Path) -> bool:
    """Whether the directory ships any plugin manifest at all."""
    return any((plugin_dir / name).exists() for name in MANIFEST_FILENAMES)


def official_plugin_dirs() -> tuple[list[str], list[str]]:
    """Split ``plugins/`` into gated plugins and ones that escaped the gate.

    Directories whose name is not a valid Python identifier (``example-plugin``)
    are never gated: mypy refuses to analyse a package it cannot name, so such
    a tree can never be part of a typing gate regardless of its manifest.

    Returns:
        ``(official, unsigned)``. ``official`` is every directory whose manifest
        declares ``integrity_sha256`` — the plugins this repo signs and ships.
        ``unsigned`` is every *other* manifest-bearing directory outside
        :data:`UNSIGNED_BY_DESIGN`, and it is a **failure**: because the roster
        is derived from that field, an official plugin that lost its hash would
        otherwise drop out of the gate silently.
    """
    if not PLUGINS_ROOT.is_dir():
        return [], []
    git = shutil.which("git")
    if not git:
        print("warning: git not found; no official plugins discovered", file=sys.stderr)
        return [], []
    found: list[str] = []
    unsigned: list[str] = []
    raw = subprocess.run(
        [git, "ls-files", "plugins/*/manifest.y*ml", "plugins/*/manifest.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    plugin_dirs = sorted({(REPO_ROOT / item).parent for item in raw})
    for plugin_dir in plugin_dirs:
        if plugin_dir.name.startswith((".", "_")):
            continue
        relative = plugin_dir.relative_to(REPO_ROOT).as_posix()
        if _manifest_declares_integrity(plugin_dir):
            if plugin_dir.name.isidentifier():
                found.append(relative)
            continue
        if plugin_dir.name in UNSIGNED_BY_DESIGN or not _has_manifest(plugin_dir):
            continue
        unsigned.append(relative)
    return found, unsigned


def collect_python_files(dirs: list[str] | None = None) -> list[str]:
    """Return the Python files belonging to official plugins."""
    relative_dirs = dirs if dirs is not None else official_plugin_dirs()[0]
    files: list[str] = []
    for relative_dir in relative_dirs:
        plugin_dir = REPO_ROOT / relative_dir
        for path in sorted(plugin_dir.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if _is_excluded(rel):
                continue
            files.append(rel)
    return files


def main() -> int:
    """CLI entrypoint."""
    official, unsigned = official_plugin_dirs()
    if unsigned:
        print(
            "Unsigned plugin(s) under plugins/ — the typing gate derives its "
            "roster from `integrity_sha256`, so these would escape it entirely:",
            file=sys.stderr,
        )
        for relative in unsigned:
            print(
                f"  - {relative}: manifest declares no integrity_sha256",
                file=sys.stderr,
            )
        print(
            "Sign it (`baselith plugin sign <path>` or "
            "scripts/sign_changed_plugins.py), or add it to UNSIGNED_BY_DESIGN "
            "in this script with a reason.",
            file=sys.stderr,
        )
        return 1

    files = collect_python_files(official)
    if not files:
        print("No official plugin Python files found.", file=sys.stderr)
        return 1

    # NOTE: ``--warn-unused-ignores`` is intentionally omitted. Under
    # ``--ignore-missing-imports`` + ``--follow-imports=skip`` mypy cannot
    # distinguish a legit optional-dep ignore from a stale one, so the two
    # flags together generate false-positives on plugins that legitimately
    # guard optional imports (e.g. playwright_stealth, psutil, prometheus).
    #
    # ``--disable-error-code=import-untyped`` silences installed-but-stubless
    # libraries (e.g. PyYAML), which ``--ignore-missing-imports`` does not
    # cover. Inline ``# type: ignore[import-untyped]`` was unreliable across
    # mypy versions when combined with ``--follow-imports=skip``.
    try:
        base_cmd = mypy_base_command()
    except MypyNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    cmd = [
        *base_cmd,
        "--ignore-missing-imports",
        "--follow-imports=skip",
        "--disable-error-code=import-untyped",
        "--no-error-summary",
        *files,
    ]
    completed = subprocess.run(cmd, cwd=REPO_ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
