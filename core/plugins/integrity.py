"""Plugin integrity verification.

Provides SHA-256 hashing of plugin source trees and verification against an
``integrity_sha256`` field declared in the plugin manifest.

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
current surface only. Re-sign with ``baselith plugin sign <path>`` (or the
``sign-changed-plugins`` pre-commit hook) to clear the warning.

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
"""

from __future__ import annotations

import hashlib
import logging
import os
from enum import IntEnum
from pathlib import Path

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


CURRENT_HASH_SURFACE = HashSurface.V3_SHIPPED
# Superseded surfaces accepted (with a warning) outside strict mode, newest
# first so the closest match is reported.
_LEGACY_SURFACES: tuple[HashSurface, ...] = (
    HashSurface.V2_BUILD,
    HashSurface.V1_SOURCE,
)

_HASHED_SUFFIXES = frozenset({".py", ".pyi"})
# Build/packaging files steer ``pip install`` (build backend selection,
# dependency pins): leaving them unhashed would let a tree whose ``*.py``
# files still match the signature execute tampered build config at install
# time. Names are matched case-insensitively. The plugin manifest itself
# (manifest.yaml|yml|json) stays excluded so the publisher can inject
# ``integrity_sha256`` after computing the digest.
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

_EXCLUDED_DIRS = frozenset({"__pycache__", ".git", "node_modules"})
# Pre-V3 the whole ``ui/`` tree was excluded — which left the compiled,
# shipped dashboard bundle outside the signature. Kept here only to
# reproduce V1/V2 digests byte-for-byte.
_LEGACY_EXCLUDED_DIRS = _EXCLUDED_DIRS | {"ui"}
# From V3 on, ``ui/`` is scoped instead of excluded: only ``ui/dist/**``
# ships (see ``[tool.setuptools.package-data]`` / ``exclude-package-data``),
# so only ``ui/dist/**`` is hashed. ``ui/src``, ``ui/node_modules`` and the
# tsconfig/vite build inputs are never distributed and stay out.
_UI_DIR = "ui"
_UI_SHIPPED_SUBDIR = "dist"


def is_hashed_path(
    path: Path,
    *,
    legacy: bool = False,
    surface: HashSurface | None = None,
) -> bool:
    """Whether ``path`` belongs to the hashed surface (by name only).

    Directory exclusions (``__pycache__``, ``ui/src``, ...) are applied by
    the tree walk, not here.

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
    return parts[0] == _UI_DIR and (len(parts) < 2 or parts[1] != _UI_SHIPPED_SUBDIR)


def _compute_hash(plugin_dir: Path, *, surface: HashSurface) -> str:
    digest = hashlib.sha256()
    base = plugin_dir.resolve()
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded(path.relative_to(base).parts, surface):
            continue
        if is_hashed_path(path, surface=surface):
            files.append(path)

    for path in sorted(files, key=lambda p: p.relative_to(base).as_posix()):
        rel = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def compute_plugin_hash(plugin_dir: Path, *, surface: HashSurface | None = None) -> str:
    """Compute a deterministic SHA-256 over a plugin's executable surface.

    Hash inputs are the ``*.py``/``*.pyi`` source files, the build and
    packaging files that ``pip install`` executes or trusts
    (``pyproject.toml``, ``setup.cfg``, ``MANIFEST.in``,
    ``requirements*.txt``), declarative skill bodies (``SKILL.md``) whose
    contents reach the model's prompt, compiled extension modules and shell
    scripts, and the front-end assets that ship and are served to the
    operator (``ui/dist/**``, ``static/**``: JS/HTML/CSS/SVG/WASM). The
    manifest is intentionally excluded so the marketplace publisher can
    inject an ``integrity_sha256`` field into the manifest after computing
    the digest without invalidating it. Each included file contributes its
    POSIX-relative path and raw bytes to the digest in sorted order so the
    hash is reproducible across platforms.

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


def is_strict_mode_enabled() -> bool:
    """Return True when ``BASELITH_REQUIRE_SIGNED_PLUGINS`` is set to a truthy value."""
    raw = os.environ.get("BASELITH_REQUIRE_SIGNED_PLUGINS", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _is_production() -> bool:
    """Whether the runtime environment is production.

    Mirrors ``core.config.environment.is_production_env`` but reads the raw env
    vars directly so this module stays stdlib-only (no pydantic/config import),
    matching the lightweight-CI constraint noted at the top of the file.
    """
    env = (
        (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development")
        .strip()
        .lower()
    )
    return env == "production"


def _allow_unsigned_in_prod() -> bool:
    """Explicit, insecure opt-out to permit unsigned plugins in production.

    The production default is fail-closed (unsigned plugins refuse to load).
    Operators who genuinely need to run an unsigned plugin in production must
    set ``BASELITH_ALLOW_UNSIGNED_IN_PROD=true`` — a deliberate, auditable
    downgrade rather than a silent one.
    """
    raw = os.environ.get("BASELITH_ALLOW_UNSIGNED_IN_PROD", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def enforce_signing_policy() -> None:
    """Surface an insecure plugin-signing posture before loading plugins.

    Production is fail-closed by default: ``verify_plugin_integrity`` refuses to
    load a plugin that has no ``integrity_sha256`` (see below). The only way to
    weaken that in production is the explicit ``BASELITH_ALLOW_UNSIGNED_IN_PROD``
    opt-out — and when it is set we log a single CRITICAL so the downgrade is
    never silent. Outside production this is a no-op (unsigned plugins load, as
    the hot-reload dev loop needs).
    """
    if not _is_production() or is_strict_mode_enabled():
        return
    if _allow_unsigned_in_prod():
        logger.critical(
            "BASELITH_ALLOW_UNSIGNED_IN_PROD is set: unsigned plugins will load "
            "UNVERIFIED in production (supply-chain risk). Remove this flag and "
            "sign all plugins (integrity_sha256) to restore fail-closed loading."
        )


def is_skip_check_enabled() -> bool:
    """Return True when ``BASELITH_SKIP_INTEGRITY_CHECK`` is set to a truthy value.

    Dev escape hatch: skips hash verification entirely so the hot-reload loop
    does not require recomputing ``integrity_sha256`` after every source edit.
    It is NEVER honored in production (returns False regardless of the flag), and
    strict mode (``BASELITH_REQUIRE_SIGNED_PLUGINS``) overrides it everywhere — a
    single env var must not be able to disable the whole supply-chain control in
    a hardened environment.
    """
    if _is_production():
        return False
    raw = os.environ.get("BASELITH_SKIP_INTEGRITY_CHECK", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# What a signature produced against each superseded surface leaves
# unprotected — surfaced verbatim in the migration warning so an operator can
# judge the residual risk without reading this module.
_SURFACE_GAPS: dict[HashSurface, str] = {
    HashSurface.V1_SOURCE: (
        "build and packaging files (pyproject.toml, requirements*.txt, ...), "
        "SKILL.md prompt bodies, native extension modules and shipped "
        "front-end assets (ui/dist, static: JS/HTML/CSS)"
    ),
    HashSurface.V2_BUILD: (
        "native extension modules (*.so/*.pyd/*.dylib), shell scripts and "
        "shipped front-end assets (ui/dist, static: JS/HTML/CSS) — code that "
        "runs on the host or in the operator's browser"
    ),
}


def _match_legacy_surface(plugin_dir: Path, expected_hash: str) -> HashSurface | None:
    """Return the superseded surface ``expected_hash`` was computed over, if any."""
    wanted = expected_hash.lower()
    for surface in _LEGACY_SURFACES:
        if _compute_hash(plugin_dir, surface=surface).lower() == wanted:
            return surface
    return None


def _handle_legacy_match(surface: HashSurface, safe_name: str, *, strict: bool) -> bool:
    """Log and decide on a signature that only matches a superseded surface."""
    gap = _SURFACE_GAPS[surface]
    if strict:
        logger.error(
            "Refusing plugin %s: integrity_sha256 matches only the superseded "
            "hash surface %s, but BASELITH_REQUIRE_SIGNED_PLUGINS demands %s. "
            "Re-sign the plugin.",
            safe_name,
            surface.name,
            CURRENT_HASH_SURFACE.name,
        )
        return False
    logger.warning(
        "Plugin %s is signed against the superseded hash surface %s: %s are "
        "NOT covered by its signature. Re-sign the plugin to extend coverage.",
        safe_name,
        surface.name,
        gap,
    )
    return True


def verify_plugin_integrity(
    plugin_dir: Path,
    expected_hash: str | None,
    *,
    strict: bool | None = None,
) -> bool:
    """Verify a plugin directory against its declared manifest hash.

    Args:
        plugin_dir: Plugin directory.
        expected_hash: Hex SHA-256 declared in ``manifest.integrity_sha256``,
            or ``None`` if absent.
        strict: Override for strict mode. Defaults to the
            ``BASELITH_REQUIRE_SIGNED_PLUGINS`` environment flag.

    Returns:
        ``True`` when the plugin is permitted to load, ``False`` otherwise.
    """
    if strict is None:
        strict = is_strict_mode_enabled()

    # Directory and manifest values are untrusted input: escape them so a
    # crafted name or hash cannot forge extra log entries. Imported lazily to
    # keep this module importable by lightweight tooling.
    from core.utils.logsafe import sanitize_log_value

    safe_name = sanitize_log_value(plugin_dir.name)

    if is_skip_check_enabled() and not strict:
        logger.warning(
            "Plugin %s integrity check SKIPPED (BASELITH_SKIP_INTEGRITY_CHECK=true). "
            "Never enable this flag in production.",
            safe_name,
        )
        return True

    if not expected_hash:
        if strict:
            logger.error(
                "Refusing to load unsigned plugin %s: integrity_sha256 missing "
                "and BASELITH_REQUIRE_SIGNED_PLUGINS is enabled.",
                safe_name,
            )
            return False
        # Fail-closed in production by default: an unsigned plugin is a
        # supply-chain risk, so refuse it unless an operator sets the explicit
        # BASELITH_ALLOW_UNSIGNED_IN_PROD opt-out. Outside production, unsigned
        # plugins still load (dev/hot-reload convenience).
        if _is_production() and not _allow_unsigned_in_prod():
            logger.error(
                "Refusing to load unsigned plugin %s in production: "
                "integrity_sha256 missing. Sign the plugin or set "
                "BASELITH_ALLOW_UNSIGNED_IN_PROD=true to override (insecure).",
                safe_name,
            )
            return False
        logger.info(
            "Plugin %s has no integrity_sha256 in manifest; loading anyway.",
            safe_name,
        )
        return True

    actual_hash = compute_plugin_hash(plugin_dir)
    if actual_hash.lower() != expected_hash.lower():
        # Migration path: a signature produced against a superseded surface
        # (see HashSurface) still loads outside strict mode, with a warning
        # naming what it fails to cover. Strict mode demands the current
        # surface. Re-sign with ``baselith plugin sign`` /
        # ``scripts/sign_changed_plugins.py`` to clear the warning.
        matched = _match_legacy_surface(plugin_dir, expected_hash)
        if matched is not None:
            return _handle_legacy_match(matched, safe_name, strict=strict)
        logger.error(
            "Plugin %s integrity check FAILED: manifest=%s computed=%s",
            safe_name,
            sanitize_log_value(expected_hash, max_length=80),
            actual_hash,
        )
        return False

    logger.debug("Plugin %s integrity verified.", safe_name)
    return True
