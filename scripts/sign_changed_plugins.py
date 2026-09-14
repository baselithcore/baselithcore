#!/usr/bin/env python
"""Re-sign plugins — from the staged git diff, or from explicit paths.

Two callers:

* the ``sign-changed-plugins`` pre-commit hook runs it with no arguments, and
  it re-signs every plugin under ``plugins/`` whose hashed files appear in the
  staged change set, staging the rewritten manifests so one commit carries both
  the source change and the matching digest;
* a human (or another tool) runs ``python scripts/sign_changed_plugins.py
  plugins/<name>`` — or ``--all`` — to re-sign a tree on demand. In that mode
  the git index is never touched.

What gets written
-----------------
``integrity_sha256``  the digest over the plugin's executable surface, which
                      from hash surface V5 includes the canonicalised manifest.
``hash_surface_version``
                      the surface the digest was computed under, so a reader
                      (and the marketplace) can tell a V5 signature from a V4
                      one without recomputing anything.
``signature_ed25519`` the publisher signature over that digest — written when
                      a hex Ed25519 private key is available in
                      ``BASELITH_PLUGIN_SIGNING_KEY``, and otherwise **blanked
                      with a loud warning**. Leaving a signature that attests a
                      hash the manifest no longer carries is worse than having
                      none: it reads as signed and verifies as nothing.

Manifests are rewritten line-by-line, so comments and formatting survive —
they carry the permission rationale and must not be dumped away.

Behaviour:
  * Only signs plugins that already declare ``integrity_sha256``. Unsigned
    plugins are left untouched.
  * Idempotent: when every field already holds the right value the file is not
    rewritten (and, in hook mode, not re-staged).

Exit status:
  0 — every relevant plugin is signed (or unchanged).
  1 — a manifest could not be parsed, signed or rewritten.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess  # nosec B404 — fixed argv list, no shell
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

try:
    import yaml
except ImportError:
    print("PyYAML required for plugin auto-signing", file=sys.stderr)
    sys.exit(1)


ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = ROOT / "plugins"

#: Environment variable holding the hex Ed25519 private key. Matches the
#: default of ``scripts/sign_plugin_ed25519.py`` so one key serves both tools.
#: Never passed on argv: that would leak it into shell history and ``ps``.
SIGNING_KEY_ENV = "BASELITH_PLUGIN_SIGNING_KEY"


def _load_core_module(name: str) -> ModuleType:
    """Direct-load a ``core/plugins`` module without importing the package.

    ``core.plugins.__init__`` pulls in pydantic/structlog and the whole config
    stack, which is far too much for a pre-commit hook. Both modules loaded
    this way (``integrity``, ``signing``) are stdlib-only at import time by
    design — see the note at the top of ``core/plugins/integrity.py``.
    """
    spec = importlib.util.spec_from_file_location(
        f"_plugin_signer_{name}", ROOT / "core" / "plugins" / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        print(f"Could not load core/plugins/{name}.py", file=sys.stderr)
        sys.exit(1)
    module = importlib.util.module_from_spec(spec)
    # Register before executing: ``dataclasses`` with ``slots=True`` resolves
    # its annotations through ``sys.modules[cls.__module__]``, which is None
    # for a module that was never registered.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_integrity = _load_core_module("integrity")
compute_plugin_hash = _integrity.compute_plugin_hash
is_hashed_path = _integrity.is_hashed_path
is_manifest_path = _integrity.is_manifest_path
CURRENT_HASH_SURFACE = int(_integrity.CURRENT_HASH_SURFACE)
_MANIFEST_NAMES: tuple[str, ...] = tuple(_integrity.MANIFEST_FILENAMES)

_rewrite = _load_core_module("manifest_rewrite")
set_manifest_fields = _rewrite.set_manifest_fields
stale_signature_present = _rewrite.stale_signature_present
supply_chain_fields = _rewrite.supply_chain_fields
_HASH_KEY = _rewrite.HASH_KEY
_SIGNATURE_KEY = _rewrite.SIGNATURE_KEY


@dataclass(frozen=True)
class SignOutcome:
    """What signing one plugin directory did."""

    plugin: str
    changed: bool = False
    digest: str = ""
    signed: bool = False
    blanked: bool = False
    error: str | None = None


def _git(*argv: str) -> str:
    out = subprocess.run(  # nosec B603 — fixed argv, no shell
        ["git", *argv], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return out.stdout


def _stage(paths: list[str]) -> None:
    subprocess.run(  # nosec B603 — fixed argv, no shell
        ["git", "add", *paths], cwd=ROOT, check=False
    )


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
        # The manifest is part of the digest from V5 on (canonically, not by
        # its bytes), so a manifest-only edit — widening `permissions:`, say —
        # now needs a rehash just as much as a source edit does.
        if not is_hashed_path(path) and not is_manifest_path(path):
            continue
        plugins.add(PLUGINS_DIR / rel.parts[0])
    return sorted(plugins)


def _find_manifest(plugin_dir: Path) -> Path | None:
    for name in _MANIFEST_NAMES:
        candidate = plugin_dir / name
        if candidate.exists():
            return candidate
    return None


def _read_manifest(manifest: Path) -> dict[str, object]:
    text = manifest.read_text(encoding="utf-8")
    data = (
        json.loads(text) if manifest.suffix == ".json" else yaml.safe_load(text)
    ) or {}
    if not isinstance(data, dict):
        raise ValueError("manifest is not a mapping")
    return data


def _display_root(manifest: Path) -> Path:
    """Repo root when the manifest lives under it, else its own parent.

    Keeps the warning readable for both ``plugins/foo/manifest.yaml`` and a
    tree outside the checkout.
    """
    return ROOT if manifest.is_relative_to(ROOT) else manifest.parent


def _sign_digest(digest: str, private_key_hex: str) -> str:
    signing = _load_core_module("signing")
    signature: str = signing.sign_plugin_hash(digest, private_key_hex)
    return signature


def sign_plugin_dir(
    plugin_dir: Path, *, private_key_hex: str | None = None
) -> SignOutcome:
    """Recompute and write a plugin's supply-chain manifest fields.

    Args:
        plugin_dir: The plugin root directory.
        private_key_hex: Hex Ed25519 private key to sign the digest with.
            Defaults to ``BASELITH_PLUGIN_SIGNING_KEY``; when neither is set an
            existing ``signature_ed25519`` is blanked rather than left stale.

    Returns:
        A :class:`SignOutcome` describing what changed. ``error`` is set (and
        nothing is written) when the manifest cannot be read, signed or
        rewritten.
    """
    name = plugin_dir.name
    manifest = _find_manifest(plugin_dir)
    if manifest is None:
        return SignOutcome(plugin=name)
    try:
        data = _read_manifest(manifest)
    except Exception as exc:
        return SignOutcome(plugin=name, error=f"cannot read manifest: {exc}")

    # Presence of the key is the opt-in — a plugin that declares no
    # ``integrity_sha256`` at all has chosen not to be signed, and the hook
    # must not sign it behind the author's back. The *value* may legitimately
    # be blank (a manifest primed for its first signature).
    if _HASH_KEY not in data:
        return SignOutcome(plugin=name)

    digest = compute_plugin_hash(plugin_dir)
    if private_key_hex is None:
        private_key_hex = os.environ.get(SIGNING_KEY_ENV, "").strip() or None

    signed = False
    blanked = False
    signature: str | None = None
    if private_key_hex:
        try:
            signature = _sign_digest(digest, private_key_hex)
        except Exception as exc:
            return SignOutcome(
                plugin=name,
                digest=digest,
                error=f"could not sign with ${SIGNING_KEY_ENV}: {exc}",
            )
        signed = True
    elif stale_signature_present(data, digest):
        # Only when the digest actually moved. A signature over a hash the
        # manifest no longer declares is worse than no signature at all — it
        # reads as signed and verifies as nothing — but an unchanged tree's
        # signature is still perfectly good, and ``--all`` must not strip it.
        signature = ""
        blanked = True
        # Name the cause, not another tool: every signer here reads the same
        # env var, so "re-sign with <other script>" just fails again with
        # "environment variable ... is empty" and re-signs nothing.
        print(
            f"  WARNING: {manifest.relative_to(_display_root(manifest))} — "
            f"{SIGNING_KEY_ENV} is not set, so {_SIGNATURE_KEY} was BLANKED "
            "rather than left attesting a hash this manifest no longer "
            "declares. Before publishing, set the signing key and re-run:\n"
            f"    export {SIGNING_KEY_ENV}=<hex ed25519 private key>\n"
            f"    python scripts/sign_changed_plugins.py {plugin_dir}",
            file=sys.stderr,
        )

    fields = supply_chain_fields(
        data, digest, signature=signature, surface_version=CURRENT_HASH_SURFACE
    )
    if not fields:
        return SignOutcome(plugin=name, digest=digest, signed=signed)
    try:
        set_manifest_fields(manifest, fields)
    except Exception as exc:
        return SignOutcome(
            plugin=name, digest=digest, error=f"cannot rewrite manifest: {exc}"
        )
    return SignOutcome(
        plugin=name, changed=True, digest=digest, signed=signed, blanked=blanked
    )


def _all_plugin_dirs() -> list[Path]:
    return sorted(
        path
        for path in PLUGINS_DIR.iterdir()
        if path.is_dir() and not path.name.startswith(("_", "."))
    )


def _report(outcome: SignOutcome) -> None:
    if outcome.error:
        print(f"  ERROR: {outcome.plugin}: {outcome.error}", file=sys.stderr)
        return
    if not outcome.changed:
        return
    print(f"  signed {outcome.plugin}: {outcome.digest}")


def main(argv: list[str] | None = None) -> int:
    """Entry point. See the module docstring for the two calling modes."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Plugin directories to re-sign. Omit to use the staged git diff.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Re-sign every plugin under plugins/ (never touches the git index).",
    )
    args = parser.parse_args(argv)

    if args.all:
        plugin_dirs, stage = _all_plugin_dirs(), False
    elif args.paths:
        plugin_dirs, stage = [path.resolve() for path in args.paths], False
    else:
        plugin_dirs, stage = _affected_plugin_dirs(_staged_files()), True

    failed: list[str] = []
    rewritten: list[Path] = []
    for plugin_dir in plugin_dirs:
        manifest = _find_manifest(plugin_dir)
        outcome = sign_plugin_dir(plugin_dir)
        _report(outcome)
        if outcome.error:
            failed.append(outcome.plugin)
        elif outcome.changed and manifest is not None:
            rewritten.append(manifest)

    if stage and rewritten:
        _stage([str(path.relative_to(ROOT)) for path in rewritten])

    if failed:
        print(f"\nFailed to sign: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
