#!/usr/bin/env python
"""Detect drift between manifest ``integrity_sha256`` and the plugin source tree.

Iterates over every directory under ``plugins/`` (or the paths supplied on the
command line), computes the SHA-256 over each plugin's executable surface, and
compares it to the value declared in the manifest.

Exit status:
  0 — all signed plugins match their manifest hash. Unsigned plugins emit a
      warning unless ``--require-signed`` is passed.
  1 — at least one plugin drifted, or an unsigned plugin was found in
      ``--require-signed`` mode.
  2 — usage error or missing dependency.

Run via pre-commit / CI:

    python scripts/check_plugin_integrity.py
    python scripts/check_plugin_integrity.py --require-signed plugins/baselithbot

To re-sign after legitimate changes::

    baselith plugin sign plugins/<name>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    print("PyYAML is required for plugin integrity checks", file=sys.stderr)
    sys.exit(2)

# Ensure repository root on sys.path when invoked from a fresh interpreter.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.plugins.integrity import compute_plugin_hash  # noqa: E402


_MANIFEST_NAMES = ("manifest.yaml", "manifest.yml", "manifest.json")
_DEFAULT_PLUGIN_ROOT = ROOT / "plugins"


def _iter_plugin_dirs(paths: list[Path]) -> list[Path]:
    if paths:
        return [p.resolve() for p in paths if p.is_dir()]
    return sorted(
        p
        for p in _DEFAULT_PLUGIN_ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(("_", "."))
    )


def _read_manifest_hash(plugin_dir: Path) -> tuple[Path, str | None] | None:
    for name in _MANIFEST_NAMES:
        manifest = plugin_dir / name
        if not manifest.exists():
            continue
        try:
            if name.endswith(".json"):
                import json

                data = json.loads(manifest.read_text(encoding="utf-8")) or {}
            else:
                data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            print(
                f"  {plugin_dir.name}: manifest parse failed ({type(exc).__name__})",
                file=sys.stderr,
            )
            return manifest, None
        return manifest, data.get("integrity_sha256")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Plugin directories to check (default: plugins/*)",
    )
    parser.add_argument(
        "--require-signed",
        action="store_true",
        help="Fail when a plugin manifest has no integrity_sha256 entry.",
    )
    args = parser.parse_args()

    drift: list[str] = []
    unsigned: list[str] = []
    checked = 0

    for plugin_dir in _iter_plugin_dirs(args.paths):
        result = _read_manifest_hash(plugin_dir)
        if result is None:
            continue  # No manifest — not a plugin
        checked += 1
        _, expected = result
        if expected is None:
            unsigned.append(plugin_dir.name)
            level = "ERROR" if args.require_signed else "WARN"
            print(f"  {level}: {plugin_dir.name} has no integrity_sha256")
            continue
        actual = compute_plugin_hash(plugin_dir)
        if actual.lower() != expected.lower():
            drift.append(plugin_dir.name)
            print(
                f"  DRIFT: {plugin_dir.name}\n"
                f"      manifest: {expected}\n"
                f"      computed: {actual}"
            )
        else:
            print(f"  OK:    {plugin_dir.name}")

    print()
    print(f"Checked: {checked} plugin(s).")
    if drift:
        print(
            f"Drifted: {len(drift)} ({', '.join(drift)}). "
            "Re-run `baselith plugin sign <path>` after intentional changes."
        )
        return 1
    if unsigned and args.require_signed:
        print(f"Unsigned: {len(unsigned)} ({', '.join(unsigned)}).")
        return 1
    print("Plugin integrity OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
