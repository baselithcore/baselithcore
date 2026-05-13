#!/usr/bin/env python
"""Auto-sign plugins whose source changed in the staged git diff.

Pre-commit hook entrypoint. For every plugin under ``plugins/`` whose
``*.py`` / ``*.pyi`` files appear in the staged change set, recompute the
SHA-256 over the executable surface and rewrite ``manifest.integrity_sha256``
in place. The updated manifest is staged so the same commit carries both
the source change and the matching hash.

Behavior:
  * Only signs plugins that already declare ``integrity_sha256`` in their
    manifest. Unsigned plugins are left untouched.
  * Skips manifest-only changes (no source drift, no rehash needed).
  * Idempotent: when the computed hash already matches the manifest the
    file is not rewritten and not re-staged.

Exit status:
  0 — every relevant plugin is signed and staged (or unchanged).
  1 — a manifest could not be parsed or rewritten.
"""

from __future__ import annotations

import importlib.util
import subprocess  # nosec B404 — fixed argv list, no shell
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("PyYAML required for plugin auto-signing", file=sys.stderr)
    sys.exit(1)


ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = ROOT / "plugins"


# Direct-load core/plugins/integrity.py without importing the package, to
# avoid pulling pydantic/structlog into a hot pre-commit gate.
_spec = importlib.util.spec_from_file_location(
    "_integrity_signer", ROOT / "core" / "plugins" / "integrity.py"
)
if _spec is None or _spec.loader is None:
    print("Could not load core/plugins/integrity.py", file=sys.stderr)
    sys.exit(1)
_integrity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_integrity)
compute_plugin_hash = _integrity.compute_plugin_hash


_HASH_KEY = "integrity_sha256:"
_MANIFEST_NAMES = ("manifest.yaml", "manifest.yml", "manifest.json")


def _git(*argv: str) -> str:
    out = subprocess.run(  # nosec B603 — fixed argv, no shell
        ["git", *argv], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return out.stdout


def _staged_files() -> list[Path]:
    raw = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    return [ROOT / line.strip() for line in raw.splitlines() if line.strip()]


def _affected_plugin_dirs(staged: list[Path]) -> list[Path]:
    plugins: set[Path] = set()
    for path in staged:
        try:
            rel = path.resolve().relative_to(PLUGINS_DIR)
        except (ValueError, OSError):
            continue
        if not rel.parts:
            continue
        if path.suffix not in {".py", ".pyi"}:
            continue
        plugins.add(PLUGINS_DIR / rel.parts[0])
    return sorted(plugins)


def _find_manifest(plugin_dir: Path) -> Path | None:
    for name in _MANIFEST_NAMES:
        candidate = plugin_dir / name
        if candidate.exists():
            return candidate
    return None


def _rewrite_yaml_hash(manifest: Path, new_hash: str) -> bool:
    """Surgical text replacement preserving comments / formatting.

    Falls back to YAML round-trip when the manifest does not already contain
    an ``integrity_sha256`` line (rare — should not happen given the caller
    only invokes this when a hash is already declared).
    """
    text = manifest.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    rewrote = False
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith(_HASH_KEY):
            indent = line[: len(line) - len(stripped)]
            out.append(f"{indent}{_HASH_KEY} {new_hash}\n")
            rewrote = True
        else:
            out.append(line)
    if not rewrote:
        return False
    manifest.write_text("".join(out), encoding="utf-8")
    return True


def _rewrite_json_hash(manifest: Path, new_hash: str) -> bool:
    import json

    data = json.loads(manifest.read_text(encoding="utf-8"))
    if "integrity_sha256" not in data:
        return False
    data["integrity_sha256"] = new_hash
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


def _current_hash(manifest: Path) -> str | None:
    if manifest.suffix == ".json":
        import json

        data = json.loads(manifest.read_text(encoding="utf-8")) or {}
        value = data.get("integrity_sha256")
        return value if isinstance(value, str) else None
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    value = data.get("integrity_sha256")
    return value if isinstance(value, str) else None


def main() -> int:
    plugin_dirs = _affected_plugin_dirs(_staged_files())
    if not plugin_dirs:
        return 0

    failed: list[str] = []
    rewritten: list[Path] = []

    for plugin_dir in plugin_dirs:
        manifest = _find_manifest(plugin_dir)
        if manifest is None:
            continue
        try:
            current = _current_hash(manifest)
        except Exception as exc:  # noqa: BLE001
            print(
                f"  ERROR: failed to read {manifest.relative_to(ROOT)}: {exc}",
                file=sys.stderr,
            )
            failed.append(plugin_dir.name)
            continue
        if current is None:
            # Plugin opted out of signing — leave untouched.
            continue
        new_hash = compute_plugin_hash(plugin_dir)
        if new_hash.lower() == current.lower():
            continue
        try:
            if manifest.suffix == ".json":
                ok = _rewrite_json_hash(manifest, new_hash)
            else:
                ok = _rewrite_yaml_hash(manifest, new_hash)
        except Exception as exc:  # noqa: BLE001
            print(
                f"  ERROR: failed to rewrite {manifest.relative_to(ROOT)}: {exc}",
                file=sys.stderr,
            )
            failed.append(plugin_dir.name)
            continue
        if not ok:
            print(
                f"  WARN:  {manifest.relative_to(ROOT)} declares integrity_sha256 but "
                "the line could not be rewritten in place.",
                file=sys.stderr,
            )
            failed.append(plugin_dir.name)
            continue
        rewritten.append(manifest)
        print(f"  signed {plugin_dir.name}: {new_hash}")

    if rewritten:
        rel_paths = [str(p.relative_to(ROOT)) for p in rewritten]
        subprocess.run(  # nosec B603 — fixed argv, no shell
            ["git", "add", *rel_paths], cwd=ROOT, check=False
        )

    if failed:
        print(f"\nFailed to sign: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
