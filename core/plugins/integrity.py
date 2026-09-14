"""Plugin integrity hashing — what a plugin signature actually covers.

Provides SHA-256 hashing of plugin source trees. The verification policy that
consumes the digest (strict mode, the production fail-closed default, the
legacy-surface migration path) lives in :mod:`core.plugins.integrity_policy`
and is re-exported from here, so ``from core.plugins.integrity import
verify_plugin_integrity`` keeps working.

Operators may enforce signed plugins by setting the environment variable
``BASELITH_REQUIRE_SIGNED_PLUGINS=true``. When strict mode is active, plugins
without a manifest hash are rejected at load time.

Hashed-surface versioning
-------------------------
The set of files that feed the digest has grown over time (see
:class:`HashSurface`). Widening it invalidates every previously computed
signature, so :func:`verify_plugin_integrity` re-computes the older surfaces
as a fallback: a plugin signed against a superseded surface still loads
outside strict mode, with a warning naming what its signature does *not*
cover. Strict mode (``BASELITH_REQUIRE_SIGNED_PLUGINS=true``) accepts the
current surface only. Re-sign with ``baselith plugin sign <path>`` (or
``python scripts/sign_changed_plugins.py <path>``) to clear the warning.

The manifest is signed too (V5)
-------------------------------
Up to V4 the manifest was deliberately excluded from the digest so a publisher
could inject ``integrity_sha256`` after computing it. The price was that the
manifest — which decides the plugin's *name*, its declared ``permissions``
(network egress, tool and secret grants), its ``python_dependencies`` and its
``min_core_version`` — was unsigned: anyone able to edit ``manifest.yaml``
could widen a signed plugin's egress without breaking the hash or the Ed25519
signature over it.

V5 folds a *canonical projection* of the manifest into the digest instead of
its bytes: the parsed mapping with ``integrity_sha256``, ``signature_ed25519``
and ``hash_surface_version`` removed, dumped as compact sorted JSON. Injection
therefore still works (those three keys are exactly the self-referential ones),
comments and key order stay free, and every other key is covered.

Shipped front-end assets (0.27)
-------------------------------
``ui/dist/**`` is compiled JS/HTML served by the operator console and is
packaged into the wheel and the marketplace archive; it is now hashed. The
rest of ``ui/`` (``node_modules``, ``src``, tsconfig/vite build inputs) is
build input that never ships, mirroring ``[tool.setuptools.exclude-package-data]``
in the plugin's ``pyproject.toml``, and stays out of the digest.

Consequence for developers: building the dashboard (``npm run build``) adds
files to the hashed surface and therefore changes the plugin hash. A tree
whose ``ui/dist/`` was built after signing must be re-signed — or loaded with
``BASELITH_SKIP_INTEGRITY_CHECK=true`` (dev only, inert in production).

Lightweight loading
-------------------
``scripts/check_plugin_integrity.py`` and ``scripts/sign_changed_plugins.py``
load *this file* directly (``importlib.util.spec_from_file_location``) so a CI
gate needs only ``hashlib``/``pathlib``/PyYAML rather than the whole
pydantic + structlog stack that ``core.plugins.__init__`` pulls in. Everything
those gates need — :func:`compute_plugin_hash`, :func:`is_hashed_path`,
:func:`is_manifest_path` — is therefore defined here with stdlib imports only.
In that mode the relative import of the policy half at the bottom of this file
cannot resolve (there is no package), which is caught and ignored: the gates
never verify, they only hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
from enum import IntEnum
from pathlib import Path
from typing import Any

# Use stdlib logging here (rather than ``core.observability.logging``) so this
# module can be loaded by lightweight CI tooling without dragging in
# ``pydantic``/``structlog``/the full config stack.
logger = logging.getLogger(__name__)


class HashSurface(IntEnum):
    """Generations of the hashed file surface, oldest first.

    Members are ordered so a newer surface always covers a superset of the
    previous one. ``CURRENT_HASH_SURFACE`` is what signing tools produce;
    the older members exist only so signatures created by earlier releases
    can still be recognised (and reported) at verification time.
    """

    V1_SOURCE = 1
    """Pre-0.17: ``*.py`` / ``*.pyi`` only."""

    V2_BUILD = 2
    """0.17-0.26: adds build/packaging files and ``SKILL.md`` bodies."""

    V3_SHIPPED = 3
    """0.27+: adds shipped executables and served front-end assets."""

    V4_UI_EXPORT = 4
    """0.31+: also covers front-end bundles a build tool writes somewhere other
    than ``ui/dist`` — a Next.js ``output: 'export'`` console in ``ui/out``, a
    Create React App build in ``ui/build``. V3 hardcoded ``dist``, so a plugin
    whose toolchain picks a different directory shipped a console the operator
    executes in their browser and no signature covered."""

    V5_MANIFEST = 5
    """0.33+: adds the canonicalised manifest. Up to V4 the manifest was the
    one file a signed plugin could rewrite freely — which meant its declared
    ``permissions`` (egress, tools, secrets), ``python_dependencies``,
    ``min_core_version`` and ``name`` were attacker-controlled on a tree whose
    hash and Ed25519 signature both still verified."""


CURRENT_HASH_SURFACE = HashSurface.V5_MANIFEST
# Superseded surfaces accepted (with a warning) outside strict mode, newest
# first so the closest match is reported.
_LEGACY_SURFACES: tuple[HashSurface, ...] = (
    HashSurface.V4_UI_EXPORT,
    HashSurface.V3_SHIPPED,
    HashSurface.V2_BUILD,
    HashSurface.V1_SOURCE,
)

_HASHED_SUFFIXES = frozenset({".py", ".pyi"})
# Build/packaging files steer ``pip install`` (build backend selection,
# dependency pins): leaving them unhashed would let a tree whose ``*.py``
# files still match the signature execute tampered build config at install
# time. Names are matched case-insensitively.
_HASHED_BUILD_FILENAMES = frozenset({"pyproject.toml", "setup.cfg", "manifest.in"})
# Declarative skill bodies (SKILL.md) are injected into agent prompts on
# activation — an unhashed skill file would let a tree whose ``*.py`` files
# still match the signature feed tampered instructions to the model
# (prompt-injection surface). Hash them like source.
_HASHED_PROMPT_FILENAMES = frozenset({"skill.md"})
# Compiled extension modules the Python runtime dlopen()s, and shell scripts
# shipped for setup/entrypoint duty. Native code bypasses every Python-level
# control, so leaving it unhashed defeats the whole signature.
_HASHED_EXECUTABLE_SUFFIXES = frozenset({".so", ".pyd", ".dylib", ".sh"})
# Front-end assets served by the operator console from the plugin's own
# origin. ``.js``/``.mjs``/``.cjs``/``.wasm`` execute directly; ``.html`` can
# carry inline script; a standalone ``.svg`` opened top-level executes its
# embedded script; ``.css`` rewrites what the operator sees and clicks
# (UI-redress). All of them ship in the wheel and the marketplace archive.
_HASHED_ASSET_SUFFIXES = frozenset(
    {".js", ".mjs", ".cjs", ".wasm", ".html", ".htm", ".svg", ".css"}
)

# The plugin contract. Hashed from V5 on, but never by its bytes — see
# :func:`canonical_manifest_bytes`. Ordered: the first one present at the
# plugin root is the manifest, mirroring the loader's own lookup.
MANIFEST_FILENAMES: tuple[str, ...] = ("manifest.yaml", "manifest.yml", "manifest.json")
# Keys removed from the canonical projection because they describe the digest
# rather than the plugin: a publisher computes the hash, signs it, stamps the
# surface version, and writes all three back into the very file being hashed.
_BLANKED_MANIFEST_KEYS: tuple[str, ...] = (
    "integrity_sha256",
    "signature_ed25519",
    "hash_surface_version",
)
# Digest label for the canonical manifest. A NUL cannot occur in a POSIX path,
# so this can never collide with a real file's relative path.
_MANIFEST_DIGEST_LABEL = "\x00manifest"

# ``target`` is Cargo's build directory, the Rust counterpart of
# ``node_modules``: gitignored, never distributed, and full of ``.dylib`` /
# ``.so`` / ``.sh`` files that the executable-surface rules below would
# otherwise hash. Leaving it in made the signature of any plugin carrying a
# Rust component depend on whether ``cargo build`` had been run locally — the
# same tree hashed differently before and after compiling.
_EXCLUDED_DIRS = frozenset({"__pycache__", ".git", "node_modules", "target"})
# Pre-V3 the whole ``ui/`` tree was excluded — which left the compiled,
# shipped dashboard bundle outside the signature. Kept here only to
# reproduce V1/V2 digests byte-for-byte.
_LEGACY_EXCLUDED_DIRS = _EXCLUDED_DIRS | {"ui"}
# From V3 on, ``ui/`` is scoped instead of excluded: only the compiled bundle
# ships (see ``[tool.setuptools.package-data]`` / ``exclude-package-data``), so
# only the bundle is hashed. ``ui/src``, ``ui/node_modules`` and the
# tsconfig/vite build inputs are never distributed and stay out.
#
# V3 hardcoded ``dist``, which is Vite's default and wrong for everything else:
# a Next.js static export lands in ``ui/out`` and Create React App writes
# ``ui/build``. Those consoles shipped and were served while no signature
# covered a byte of them. V4 covers all three.
_UI_DIR = "ui"
_UI_SHIPPED_SUBDIRS_V3 = frozenset({"dist"})
_UI_SHIPPED_SUBDIRS = frozenset({"dist", "out", "build"})


def is_manifest_path(path: Path) -> bool:
    """Whether ``path`` names a plugin manifest.

    Args:
        path: Any path; only its file name is inspected.

    Returns:
        ``True`` for ``manifest.yaml``/``.yml``/``.json`` (case-insensitively).
    """
    return path.name.lower() in MANIFEST_FILENAMES


def find_manifest_file(plugin_dir: Path) -> Path | None:
    """Return the plugin's manifest, in the loader's own lookup order.

    Args:
        plugin_dir: Plugin root directory.

    Returns:
        The first existing ``manifest.yaml``/``.yml``/``.json``, or ``None``.
    """
    for name in MANIFEST_FILENAMES:
        candidate = plugin_dir / name
        if candidate.is_file():
            return candidate
    return None


def is_hashed_path(
    path: Path,
    *,
    legacy: bool = False,
    surface: HashSurface | None = None,
) -> bool:
    """Whether ``path`` contributes its **raw bytes** to the digest (by name only).

    Directory exclusions (``__pycache__``, ``ui/src``, ...) are applied by
    the tree walk, not here. The manifest is deliberately excluded from this
    predicate even at V5+: from V5 on it is covered, but by its canonical
    parsed form rather than its bytes (see :func:`canonical_manifest_bytes`),
    so callers that want "does this file move the digest?" must also test
    :func:`is_manifest_path`.

    Args:
        path: File path whose name/suffix is inspected.
        legacy: Backwards-compatible shorthand for
            ``surface=HashSurface.V1_SOURCE``. Ignored when ``surface`` is
            given explicitly.
        surface: Surface generation to evaluate against. Defaults to
            ``CURRENT_HASH_SURFACE``.
    """
    if surface is None:
        surface = HashSurface.V1_SOURCE if legacy else CURRENT_HASH_SURFACE
    if path.suffix in _HASHED_SUFFIXES:
        return True
    if surface < HashSurface.V2_BUILD:
        return False
    name = path.name.lower()
    if name in _HASHED_BUILD_FILENAMES or name in _HASHED_PROMPT_FILENAMES:
        return True
    if name.startswith("requirements") and path.suffix == ".txt":
        return True
    if surface < HashSurface.V3_SHIPPED:
        return False
    suffix = path.suffix.lower()
    return suffix in _HASHED_EXECUTABLE_SUFFIXES or suffix in _HASHED_ASSET_SUFFIXES


def _is_excluded(parts: tuple[str, ...], surface: HashSurface) -> bool:
    """Whether a plugin-relative path is outside the walk for ``surface``."""
    if surface < HashSurface.V3_SHIPPED:
        return any(part in _LEGACY_EXCLUDED_DIRS for part in parts)
    if any(part in _EXCLUDED_DIRS for part in parts):
        return True
    # Everything under ``ui/`` except the compiled, shipped bundle is build
    # input that never leaves the developer's machine.
    shipped = (
        _UI_SHIPPED_SUBDIRS
        if surface >= HashSurface.V4_UI_EXPORT
        else _UI_SHIPPED_SUBDIRS_V3
    )
    return parts[0] == _UI_DIR and (len(parts) < 2 or parts[1] not in shipped)


def _json_safe(value: Any) -> Any:
    """Coerce a parsed YAML/JSON value into something ``json.dumps`` can sort.

    YAML admits non-string mapping keys and scalars JSON has no notion of
    (``date``, ``datetime``). Both are rendered through ``str`` so the digest
    stays computable and deterministic instead of raising on an exotic
    manifest.
    """
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


def canonical_manifest_bytes(manifest: Path) -> bytes:
    """Render a manifest as the bytes that feed the V5 digest.

    The manifest is parsed, the three self-referential supply-chain keys
    (``integrity_sha256``, ``signature_ed25519``, ``hash_surface_version``) are
    dropped, and what remains is dumped as compact JSON with sorted keys. So:

    * injecting a hash/signature/surface version after computing the digest
      leaves the digest unchanged — the publishing workflow still works;
    * comments, key order, quoting style and YAML-vs-JSON spelling are free;
    * every other key — ``permissions``, ``python_dependencies``,
      ``min_core_version``, ``name``, ``entry_point`` — is covered.

    Args:
        manifest: Path to the plugin's manifest file.

    Returns:
        The canonical byte string. A manifest that will not parse contributes
        its raw bytes instead, so a broken manifest is still covered (and the
        tree still hashes deterministically) rather than being skipped. Those
        bytes cannot collide with the canonical form: canonical JSON is valid
        YAML, so anything that reaches this fallback would not have.
    """
    raw = manifest.read_bytes()
    try:
        import yaml

        data = yaml.safe_load(raw.decode("utf-8"))
    except Exception:  # silent-ok: raw bytes cover an unparseable manifest
        return raw
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if k not in _BLANKED_MANIFEST_KEYS}
    return json.dumps(
        _json_safe(data),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def read_declared_surface(plugin_dir: Path) -> int | None:
    """Read ``hash_surface_version`` from a plugin's manifest.

    Advisory only: the key sits outside the digest (it has to — it is written
    after the digest is computed), so it is documentation for tooling and for
    the operator, never an input to verification. Verification always tries
    the current surface first and falls back through the superseded ones, so a
    tampered version number buys an attacker nothing.

    Args:
        plugin_dir: Plugin root directory.

    Returns:
        The declared surface generation, or ``None`` when absent or unreadable.
    """
    manifest = find_manifest_file(plugin_dir)
    if manifest is None:
        return None
    try:
        import yaml

        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except Exception:  # silent-ok: advisory field; unreadable == absent
        return None
    if not isinstance(data, dict):
        return None
    try:
        return int(data["hash_surface_version"])
    except (KeyError, TypeError, ValueError):
        return None


def _compute_hash(plugin_dir: Path, *, surface: HashSurface) -> str:
    digest = hashlib.sha256()
    base = plugin_dir.resolve()
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded(path.relative_to(base).parts, surface):
            continue
        # The manifest never contributes its bytes: at V5+ it contributes its
        # canonical form below, and before V5 it contributed nothing at all.
        if is_manifest_path(path):
            continue
        if is_hashed_path(path, surface=surface):
            files.append(path)

    for path in sorted(files, key=lambda p: p.relative_to(base).as_posix()):
        rel = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")

    if surface >= HashSurface.V5_MANIFEST:
        manifest = find_manifest_file(base)
        if manifest is not None:
            digest.update(_MANIFEST_DIGEST_LABEL.encode("utf-8"))
            digest.update(b"\0")
            digest.update(canonical_manifest_bytes(manifest))
            digest.update(b"\0")
    return digest.hexdigest()


def compute_plugin_hash(plugin_dir: Path, *, surface: HashSurface | None = None) -> str:
    """Compute a deterministic SHA-256 over a plugin's executable surface.

    Hash inputs are the ``*.py``/``*.pyi`` source files, the build and
    packaging files that ``pip install`` executes or trusts
    (``pyproject.toml``, ``setup.cfg``, ``MANIFEST.in``,
    ``requirements*.txt``), declarative skill bodies (``SKILL.md``) whose
    contents reach the model's prompt, compiled extension modules and shell
    scripts, the front-end assets that ship and are served to the operator
    (``ui/{dist,out,build}/**``, ``static/**``: JS/HTML/CSS/SVG/WASM), and —
    from V5 — the canonicalised manifest (:func:`canonical_manifest_bytes`),
    which carries the plugin's declared permissions, dependencies and version
    floor. Each included file contributes its POSIX-relative path and raw
    bytes to the digest in sorted order so the hash is reproducible across
    platforms; the manifest contributes last, under a reserved label.

    Args:
        plugin_dir: Resolved path to the plugin root directory.
        surface: Surface generation to hash. Defaults to
            ``CURRENT_HASH_SURFACE``; older generations exist only for
            verifying signatures produced by earlier releases.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return _compute_hash(plugin_dir, surface=surface or CURRENT_HASH_SURFACE)


def compute_legacy_plugin_hash(plugin_dir: Path) -> str:
    """Compute the pre-0.17 digest (``*.py``/``*.pyi`` only).

    Kept so plugins signed before the hashed surface was extended to build
    files keep loading (outside strict mode) until they are re-signed.
    """
    return _compute_hash(plugin_dir, surface=HashSurface.V1_SOURCE)


try:
    # The verification policy half. Relative, so this raises ImportError when
    # the file is direct-loaded outside the package by the CI gates (see the
    # module docstring) — which is fine: those gates only hash.
    from .integrity_policy import (
        enforce_signing_policy as enforce_signing_policy,
    )
    from .integrity_policy import (
        is_skip_check_enabled as is_skip_check_enabled,
    )
    from .integrity_policy import (
        is_strict_mode_enabled as is_strict_mode_enabled,
    )
    from .integrity_policy import (
        verify_plugin_integrity as verify_plugin_integrity,
    )
except ImportError:
    # Only the direct-load path may fail here. With a package context an
    # ImportError means something is genuinely broken *inside*
    # ``integrity_policy`` — swallowing that would silently delete
    # ``verify_plugin_integrity`` from the public surface and load every
    # plugin unverified.
    if __package__:
        raise


__all__ = [
    "CURRENT_HASH_SURFACE",
    "MANIFEST_FILENAMES",
    "HashSurface",
    "canonical_manifest_bytes",
    "compute_legacy_plugin_hash",
    "compute_plugin_hash",
    "enforce_signing_policy",
    "find_manifest_file",
    "is_hashed_path",
    "is_manifest_path",
    "is_skip_check_enabled",
    "is_strict_mode_enabled",
    "read_declared_surface",
    "verify_plugin_integrity",
]
