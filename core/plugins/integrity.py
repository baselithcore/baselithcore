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
_EXCLUDED_DIRS = frozenset({"__pycache__", ".git", "node_modules", "ui"})


def compute_plugin_hash(plugin_dir: Path) -> str:
    """Compute a deterministic SHA-256 over a plugin's executable surface.

    Hash inputs are restricted to ``*.py``/``*.pyi`` source files. The
    manifest is intentionally excluded so the marketplace publisher can
    inject an ``integrity_sha256`` field into the manifest after computing
    the digest without invalidating it. Each included file contributes its
    POSIX-relative path and raw bytes to the digest in sorted order so the
    hash is reproducible across platforms.

    Args:
        plugin_dir: Resolved path to the plugin root directory.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    digest = hashlib.sha256()
    base = plugin_dir.resolve()
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _EXCLUDED_DIRS for part in path.relative_to(base).parts):
            continue
        if path.suffix in _HASHED_SUFFIXES:
            files.append(path)

    for path in sorted(files, key=lambda p: p.relative_to(base).as_posix()):
        rel = path.relative_to(base).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


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


def _fail_on_unsigned_in_prod_enabled() -> bool:
    """Whether to hard-fail (vs. warn) on unsigned plugins in production."""
    raw = os.environ.get("BASELITH_FAIL_ON_UNSIGNED_IN_PROD", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def enforce_signing_policy() -> None:
    """Surface an insecure plugin-signing posture before loading plugins.

    Running in production without ``BASELITH_REQUIRE_SIGNED_PLUGINS`` means any
    code dropped into the plugins directory loads unverified — a supply-chain
    risk. This is called once at the start of plugin loading.

    Default behavior is a single CRITICAL log (no raise), so it cannot break an
    existing production deployment that already runs unsigned plugins. Operators
    who want fail-closed semantics set ``BASELITH_FAIL_ON_UNSIGNED_IN_PROD=true``
    to turn the warning into a hard error. Outside production it is a no-op.

    Raises:
        RuntimeError: only when in production, strict mode is off, and
            ``BASELITH_FAIL_ON_UNSIGNED_IN_PROD`` is enabled.
    """
    if not _is_production() or is_strict_mode_enabled():
        return
    message = (
        "Plugin signing is NOT enforced in a production environment. "
        "Unsigned plugins will load unverified (supply-chain risk). "
        "Set BASELITH_REQUIRE_SIGNED_PLUGINS=true and sign all plugins."
    )
    if _fail_on_unsigned_in_prod_enabled():
        raise RuntimeError(message)
    logger.critical(message)


def is_skip_check_enabled() -> bool:
    """Return True when ``BASELITH_SKIP_INTEGRITY_CHECK`` is set to a truthy value.

    Dev escape hatch: skips hash verification entirely so the hot-reload loop
    does not require recomputing ``integrity_sha256`` after every source edit.
    Logs a single warning per verify call so operators cannot leave it on
    accidentally in production. Strict mode (``BASELITH_REQUIRE_SIGNED_PLUGINS``)
    overrides this — refusing to honor a skip flag in a hardened environment.
    """
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
        logger.info(
            "Plugin %s has no integrity_sha256 in manifest; loading anyway.",
            plugin_dir.name,
        )
        return True

    actual_hash = compute_plugin_hash(plugin_dir)
    if actual_hash.lower() != expected_hash.lower():
        logger.error(
            "Plugin %s integrity check FAILED: manifest=%s computed=%s",
            plugin_dir.name,
            expected_hash,
            actual_hash,
        )
        return False

    logger.debug("Plugin %s integrity verified.", plugin_dir.name)
    return True
