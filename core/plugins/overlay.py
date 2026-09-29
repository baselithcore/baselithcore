"""Plugin overlay directory: verified plugin trees that shadow bundled ones.

A plugin update never mutates the bundled ``plugins/`` tree (an image layer on
Kubernetes, a git checkout on a host). The verified release lands in
``$BASELITH_PLUGIN_OVERLAY_DIR/.store/<name>-<version>/`` and ``<overlay>/<name>``
points at it; this module makes ``plugins.<name>`` resolve there.

Registration must precede the first import of any ``plugins.<name>`` module —
``core.api.factory`` already imports ``plugins.api_routers`` — so
``plugins/__init__.py`` calls :func:`register_overlay_packages` itself. Keep
this module light: stdlib plus the few ``core.plugins`` modules below, none
of which imports a plugin.

Every entry must carry a publisher signature that verifies against the
deployment's trust store, whatever ``BASELITH_REQUIRE_PLUGIN_SIGNATURES`` says:
the overlay exists only to receive signed releases. A rejected entry is logged
and skipped, so the bundled plugin loads instead.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from core.plugins._links import first_symlink
from core.plugins._module_paths import ensure_parent_packages

logger = logging.getLogger(__name__)

OVERLAY_ENV = "BASELITH_PLUGIN_OVERLAY_DIR"
STORE_DIRNAME = ".store"

#: Same order as ``core.plugins.discovery.MANIFEST_FILENAMES`` (a test pins it);
#: duplicated so this module does not import discovery's logging stack.
_MANIFEST_FILENAMES = ("manifest.yaml", "manifest.yml", "manifest.json")

#: name -> overlay entry path (``<overlay>/<name>``), verified and registered.
_REGISTERED: dict[str, Path] = {}


def overlay_root() -> Path | None:
    """The configured overlay directory, or None when unset or missing."""
    raw = os.environ.get(OVERLAY_ENV, "").strip()
    if not raw:
        return None
    root = Path(raw).expanduser()
    return root if root.is_dir() else None


def candidate_overlay_dirs(root: Path) -> list[Path]:
    """Entries of ``root`` that look like plugins (unresolved paths)."""
    found: list[Path] = []
    for entry in sorted(root.iterdir()):
        if entry.name.startswith((".", "_")) or not entry.is_dir():
            continue
        if (entry / "__init__.py").exists() or (entry / "plugin.py").exists():
            found.append(entry)
    return found


def _manifest(entry: Path) -> Path | None:
    for name in _MANIFEST_FILENAMES:
        candidate = entry / name
        if candidate.is_file():
            return candidate
    return None


def _read_manifest(path: Path) -> dict[str, object]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def verify_overlay_entry(entry: Path, trusted_keys: Sequence[str]) -> str | None:
    """Return None when ``entry`` may be registered, else the refusal reason."""
    from core.plugins.integrity import compute_plugin_hash
    from core.plugins.signing import verify_plugin_signature

    root = entry.parent
    resolved = entry.resolve()
    if entry.is_symlink() and not resolved.is_relative_to(
        (root / STORE_DIRNAME).resolve()
    ):
        return "escapes_store"
    # The entry may itself link into .store; nothing below it may: the hash
    # walk does not follow links, so their targets would load unverified.
    if first_symlink(resolved) is not None:
        return "symlink"
    manifest = _manifest(resolved)
    if manifest is None:
        return "manifest_missing"
    data = _read_manifest(manifest)
    if data.get("name") != entry.name:
        return "name_mismatch"
    declared = str(data.get("integrity_sha256") or "")
    signature = str(data.get("signature_ed25519") or "")
    if not declared or compute_plugin_hash(resolved) != declared:
        return "integrity_mismatch" if declared else "signature_invalid"
    if not signature or not trusted_keys:
        return "signature_invalid"
    if not verify_plugin_signature(declared, signature, list(trusted_keys)):
        return "signature_invalid"
    return None


def _trusted_public_keys() -> list[str]:
    from core.plugins.signing import load_trusted_keys

    return [key.public_key_hex for key in load_trusted_keys() if key.is_usable]


def register_overlay_packages(root: Path | None = None) -> list[str]:
    """Verify and register every overlay entry; idempotent. Returns the names."""
    base = root if root is not None else overlay_root()
    if base is None:
        return sorted(_REGISTERED)
    keys = _trusted_public_keys()
    for entry in candidate_overlay_dirs(base):
        if entry.name in _REGISTERED:
            continue
        try:
            reason = verify_overlay_entry(entry, keys)
        except Exception as exc:  # one malformed entry never blocks the rest
            logger.error(
                "Plugin overlay entry %s could not be verified (%s); skipped.",
                entry.name,
                type(exc).__name__,
            )
            reason = "verify_error"
        if reason is not None:
            logger.error(
                "Plugin overlay entry %s refused (%s); the bundled plugin loads instead.",
                entry.name,
                reason,
            )
            continue
        ensure_parent_packages(entry.name, entry)
        _REGISTERED[entry.name] = entry
    return sorted(_REGISTERED)


def registered_overlay_dirs() -> list[Path]:
    """Overlay entries registered in this process, name-sorted."""
    return [_REGISTERED[name] for name in sorted(_REGISTERED)]


def bundled_shadow_modules(bundled_root: Path) -> list[str]:
    """Modules of an overlaid plugin that were loaded from the bundled tree."""
    bundled = bundled_root.resolve()
    shadows: list[str] = []
    for name in _REGISTERED:
        prefix = f"plugins.{name}"
        for mod_name, module in list(sys.modules.items()):
            if mod_name != prefix and not mod_name.startswith(prefix + "."):
                continue
            file = getattr(module, "__file__", None)
            if file and Path(file).resolve().is_relative_to(bundled):
                shadows.append(mod_name)
    return sorted(shadows)


__all__ = [
    "OVERLAY_ENV",
    "STORE_DIRNAME",
    "bundled_shadow_modules",
    "candidate_overlay_dirs",
    "overlay_root",
    "register_overlay_packages",
    "registered_overlay_dirs",
    "verify_overlay_entry",
]
