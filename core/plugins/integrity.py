"""Plugin integrity verification.

Provides SHA-256 hashing of plugin source trees and verification against an
``integrity_sha256`` field declared in the plugin manifest.

Operators may enforce signed plugins by setting the environment variable
``BASELITH_REQUIRE_SIGNED_PLUGINS=true``. When strict mode is active, plugins
without a manifest hash are rejected at load time.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

# Use stdlib logging here (rather than ``core.observability.logging``) so this
# module can be loaded by lightweight CI tooling without dragging in
# ``pydantic``/``structlog``/the full config stack.
logger = logging.getLogger(__name__)

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
_EXCLUDED_DIRS = frozenset({"__pycache__", ".git", "node_modules", "ui"})


def is_hashed_path(path: Path, *, legacy: bool = False) -> bool:
    """Whether ``path`` belongs to the hashed surface (by name only).

    Directory exclusions (``__pycache__``, ``ui``, ...) are applied by the
    tree walk, not here. ``legacy=True`` restricts to the pre-0.17 surface.
    """
    if path.suffix in _HASHED_SUFFIXES:
        return True
    if legacy:
        return False
    name = path.name.lower()
    if name in _HASHED_BUILD_FILENAMES or name in _HASHED_PROMPT_FILENAMES:
        return True
    return name.startswith("requirements") and path.suffix == ".txt"


def _compute_hash(plugin_dir: Path, *, legacy: bool) -> str:
    digest = hashlib.sha256()
    base = plugin_dir.resolve()
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _EXCLUDED_DIRS for part in path.relative_to(base).parts):
            continue
        if is_hashed_path(path, legacy=legacy):
            files.append(path)

    for path in sorted(files, key=lambda p: p.relative_to(base).as_posix()):
        rel = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def compute_plugin_hash(plugin_dir: Path) -> str:
    """Compute a deterministic SHA-256 over a plugin's executable surface.

    Hash inputs are the ``*.py``/``*.pyi`` source files, the build and
    packaging files that ``pip install`` executes or trusts
    (``pyproject.toml``, ``setup.cfg``, ``MANIFEST.in``,
    ``requirements*.txt``), and declarative skill bodies (``SKILL.md``)
    whose contents reach the model's prompt. The manifest is intentionally
    excluded so the
    marketplace publisher can inject an ``integrity_sha256`` field into the
    manifest after computing the digest without invalidating it. Each
    included file contributes its POSIX-relative path and raw bytes to the
    digest in sorted order so the hash is reproducible across platforms.

    Args:
        plugin_dir: Resolved path to the plugin root directory.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return _compute_hash(plugin_dir, legacy=False)


def compute_legacy_plugin_hash(plugin_dir: Path) -> str:
    """Compute the pre-0.17 digest (``*.py``/``*.pyi`` only).

    Kept so plugins signed before the hashed surface was extended to build
    files keep loading (outside strict mode) until they are re-signed.
    """
    return _compute_hash(plugin_dir, legacy=True)


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

    if is_skip_check_enabled() and not strict:
        logger.warning(
            "Plugin %s integrity check SKIPPED (BASELITH_SKIP_INTEGRITY_CHECK=true). "
            "Never enable this flag in production.",
            plugin_dir.name,
        )
        return True

    if not expected_hash:
        if strict:
            logger.error(
                "Refusing to load unsigned plugin %s: integrity_sha256 missing "
                "and BASELITH_REQUIRE_SIGNED_PLUGINS is enabled.",
                plugin_dir.name,
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
                plugin_dir.name,
            )
            return False
        logger.info(
            "Plugin %s has no integrity_sha256 in manifest; loading anyway.",
            plugin_dir.name,
        )
        return True

    actual_hash = compute_plugin_hash(plugin_dir)
    if actual_hash.lower() != expected_hash.lower():
        # Migration path: accept signatures computed over the pre-0.17
        # surface (*.py/*.pyi only), except in strict mode where the full
        # build-file guarantee is required. Re-sign with
        # ``scripts/sign_changed_plugins.py`` to clear the warning.
        legacy_hash = compute_legacy_plugin_hash(plugin_dir)
        if legacy_hash.lower() == expected_hash.lower():
            if strict:
                logger.error(
                    "Refusing plugin %s: integrity_sha256 matches only the "
                    "legacy source-only surface, but "
                    "BASELITH_REQUIRE_SIGNED_PLUGINS demands the extended "
                    "surface (build/packaging files included). Re-sign the "
                    "plugin.",
                    plugin_dir.name,
                )
                return False
            logger.warning(
                "Plugin %s is signed with the legacy source-only hash: build "
                "and packaging files (pyproject.toml, requirements*.txt, ...) "
                "are NOT covered by its signature. Re-sign the plugin to "
                "extend coverage.",
                plugin_dir.name,
            )
            return True
        logger.error(
            "Plugin %s integrity check FAILED: manifest=%s computed=%s",
            plugin_dir.name,
            expected_hash,
            actual_hash,
        )
        return False

    logger.debug("Plugin %s integrity verified.", plugin_dir.name)
    return True
