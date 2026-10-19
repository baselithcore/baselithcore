#!/usr/bin/env python3
"""Gate: every plugin directory keeps the standard repository layout.

The root of ``baselith/plugins/<name>/`` is what an external developer sees
when they open the plugin's mirror repository, so it is held to the layout
every mirrored project follows (``docs/architecture/plugin_layout.md``):

* a small, fixed set of root files — the manifest, ``README.md``, the GitHub
  community files, tool configuration and the conventional plugin skeleton
  modules (``plugin.py``, ``config.py``, ``models.py``, …);
* every other Markdown page lives under ``docs/`` (the mkdocs tree), every
  other Python module inside a sub-package, every data/SQL/example file in a
  named directory;
* mirrored plugins additionally carry ``LICENSE``, ``CHANGELOG.md``,
  ``CONTRIBUTING.md``, ``SECURITY.md`` and a ``.gitignore``, and a
  proprietary plugin's ``LICENSE`` is byte-identical to ``LICENSE.proprietary``;
* no runtime state, cache or private key is tracked anywhere in the tree.

Only tracked files count (``git ls-files``), so local build output never
trips the gate. ``scripts/plugin_layout_baseline.json`` freezes the loose
root files that predate the rule: a baselined file is tolerated while it is
still loose, and the entry must go once the file has moved — the baseline is
a ratchet, never a waiver. Run ``--update-baseline`` after moving files.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_ROOT = REPO_ROOT / "baselith" / "plugins"
BASELINE_PATH = REPO_ROOT / "scripts" / "plugin_layout_baseline.json"
MIRRORS_PATH = REPO_ROOT / "configs" / "plugin_mirrors.yaml"
PROPRIETARY_LICENSE = REPO_ROOT / "LICENSE.proprietary"

MANIFEST_NAMES = frozenset({"manifest.yaml", "manifest.yml", "manifest.json"})

#: GitHub community-standard files; the only Markdown allowed at the root.
GOVERNANCE_FILES = frozenset(
    {
        "README.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "LICENSE",
    }
)
#: Tool contracts read from the project root by the tool that owns them.
TOOLING_FILES = frozenset(
    {
        ".gitignore",
        ".gitattributes",
        ".env.example",
        ".editorconfig",
        ".pre-commit-config.yaml",
        ".markdownlint.yaml",
        ".markdownlint-cli2.yaml",
        ".prettierrc",
        "mkdocs.yml",
        "pyproject.toml",
        "requirements.txt",
        "alembic.ini",
        "py.typed",
        # impeccable (design tooling) reads both from the project root.
        "DESIGN.md",
        "PRODUCT.md",
    }
)
#: The conventional plugin skeleton (``example-plugin``) plus the framework
#: seams the plugin rules name. Anything else lives inside a sub-package.
SKELETON_MODULES = frozenset(
    {
        "__init__.py",
        "plugin.py",
        "_bootstrap.py",
        "app_factory.py",
        "context_bridge.py",
        "config.py",
        "models.py",
        "events.py",
        "errors.py",
        "i18n.py",
        "security.py",
        "tenancy.py",
        "dependencies.py",
        "router.py",
        "handlers.py",
        "agent.py",
        "persistence.py",
        "memory.py",
        "utils.py",
        "metrics.py",
        "audit.py",
        "compliance.py",
        "privacy_provider.py",
        "health.py",
        "cli.py",
        "mcp_tools.py",
        "ratelimit.py",
        # the agent-plugin seams: typed answers, prompts, MCP tools, governed LLM access, the service
        "types.py",
        "schemas.py",
        "prompts.py",
        "tools.py",
        "llm.py",
        "service.py",
    }
)
#: Directories that are build output, caches or runtime state. Never tracked.
FORBIDDEN_DIRS = frozenset(
    {
        "__pycache__",
        ".ruff_cache",
        ".mypy_cache",
        ".pytest_cache",
        ".turbo",
        "node_modules",
        ".state",
        "qdrant_data",
        "vaults",
        "var",
        "logs",
    }
)
#: Private-key-looking tracked files (honeypot host keys were committed once).
SECRET_FILE = re.compile(
    r"(^|/)(.*\.(pem|key|p12|pfx)|ssh_host_[a-z0-9_]*|id_(rsa|ed25519|ecdsa))$"
)
MIRROR_REQUIRED = (
    "LICENSE",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    ".gitignore",
)


def tracked_files() -> list[str]:
    """Every tracked path under ``baselith/plugins/``, repo-relative."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", "baselith/plugins"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [p for p in out.split("\0") if p]


def mirrored_plugins() -> set[str]:
    """Directory names registered as a whole-plugin mirror."""
    if not MIRRORS_PATH.is_file():
        return set()
    raw = yaml.safe_load(MIRRORS_PATH.read_text(encoding="utf-8")) or {}
    names: set[str] = set()
    for name, spec in (raw.get("mirrors") or {}).items():
        path = (spec or {}).get("path")
        if path is None or Path(path) == Path("baselith/plugins") / name:
            names.add(str(name))
    return names


def manifest_license(plugin_dir: Path) -> str | None:
    for name in MANIFEST_NAMES:
        path = plugin_dir / name
        if path.is_file():
            match = re.search(
                r"^license:\s*['\"]?([^'\"#\s]+)",
                path.read_text(encoding="utf-8"),
                re.M,
            )
            return match.group(1) if match else None
    return None


def group_by_plugin(paths: list[str]) -> dict[str, list[str]]:
    plugins: dict[str, list[str]] = {}
    for path in paths:
        parts = path.split("/")
        if len(parts) < 4:
            continue  # baselith/plugins/__init__.py, CLAUDE.md
        plugins.setdefault(parts[2], []).append("/".join(parts[3:]))
    return {
        name: files
        for name, files in plugins.items()
        if any(f in MANIFEST_NAMES for f in files)
    }


def loose_root_files(files: list[str]) -> list[str]:
    """Root files outside the standard set, sorted."""
    loose: list[str] = []
    for rel in files:
        if "/" in rel:
            continue
        if rel in MANIFEST_NAMES or rel in GOVERNANCE_FILES or rel in TOOLING_FILES:
            continue
        if rel in SKELETON_MODULES:
            continue
        loose.append(rel)
    return sorted(loose)


def hygiene_violations(name: str, files: list[str]) -> list[str]:
    """Tracked runtime state, caches or secrets anywhere in the plugin tree."""
    problems: list[str] = []
    for rel in files:
        parts = rel.split("/")
        bad_dir = next((p for p in parts[:-1] if p in FORBIDDEN_DIRS), None)
        if bad_dir:
            problems.append(
                f"{name}/{rel}: tracked under {bad_dir}/ (build output / runtime state)"
            )
        elif SECRET_FILE.search(rel):
            problems.append(
                f"{name}/{rel}: looks like a private key — generate at runtime, never commit"
            )
    return problems


def mirror_violations(name: str, files: list[str]) -> list[str]:
    problems = [
        f"{name}/{req} missing (required for a mirrored plugin)"
        for req in MIRROR_REQUIRED
        if req not in files
    ]
    if (
        "LICENSE" in files
        and manifest_license(PLUGINS_ROOT / name) == "LicenseRef-Proprietary"
    ):
        if (
            PLUGINS_ROOT / name / "LICENSE"
        ).read_bytes() != PROPRIETARY_LICENSE.read_bytes():
            problems.append(
                f"{name}/LICENSE differs from LICENSE.proprietary (manifest says LicenseRef-Proprietary)"
            )
    return problems


def required_violations(name: str, files: list[str]) -> list[str]:
    return [
        f"{name}/{req} missing"
        for req in ("README.md", "__init__.py")
        if req not in files
    ]


def load_baseline() -> dict[str, list[str]]:
    if not BASELINE_PATH.is_file():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def check(update_baseline: bool = False) -> int:
    plugins = group_by_plugin(tracked_files())
    mirrored = mirrored_plugins()
    baseline = load_baseline()
    failures: list[str] = []
    new_baseline: dict[str, list[str]] = {}

    for name in sorted(plugins):
        files = plugins[name]
        loose = loose_root_files(files)
        frozen = set(baseline.get(name, []))
        if loose:
            new_baseline[name] = loose
        for rel in loose:
            if rel not in frozen:
                failures.append(
                    f"{name}/{rel}: loose root file — Markdown goes under docs/, a module into a "
                    "sub-package, data/SQL/examples into a named directory"
                )
        for rel in sorted(frozen - set(loose)):
            failures.append(
                f"{name}/{rel}: baseline entry is stale (file moved or removed) — drop it from {BASELINE_PATH.name}"
            )
        failures.extend(required_violations(name, files))
        failures.extend(hygiene_violations(name, files))
        if name in mirrored:
            failures.extend(mirror_violations(name, files))

    for name in sorted(set(baseline) - set(plugins)):
        failures.append(
            f"{name}: baseline entry for a plugin that no longer exists — drop it from {BASELINE_PATH.name}"
        )

    if update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(new_baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            f"baseline rewritten: {sum(len(v) for v in new_baseline.values())} loose root files in {len(new_baseline)} plugins"
        )
        return 0

    if failures:
        print("Plugin layout gate failed:\n", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        print(
            f"\n{len(failures)} problem(s). Standard: the 'Standard plugin layout' rule in CLAUDE.md. "
            "After legitimately moving a baselined file: python scripts/check_plugin_layout.py --update-baseline",
            file=sys.stderr,
        )
        return 1
    frozen_total = sum(len(v) for v in baseline.values())
    print(
        f"Plugin layout OK: {len(plugins)} plugins, {frozen_total} baselined loose root files left to move"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="freeze the current loose root files",
    )
    args = parser.parse_args(argv)
    return check(update_baseline=args.update_baseline)


if __name__ == "__main__":
    sys.exit(main())
