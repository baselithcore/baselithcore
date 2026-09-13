"""Plugin integrity *policy* — what the framework does with the digest.

:mod:`core.plugins.integrity` decides what a signature covers; this module
decides whether a plugin whose digest does or does not match is allowed to
load. It is a separate file for two reasons: the 500-line cap, and the fact
that ``integrity.py`` is direct-loaded by lightweight CI gates that must not
drag in anything beyond ``hashlib``/``pathlib``/PyYAML.

Everything public here is re-exported from :mod:`core.plugins.integrity`, so
``from core.plugins.integrity import verify_plugin_integrity`` (the shape the
loader, the app-setup path and the marketplace installer use) still works.

Every import of ``core.plugins.integrity`` below is deliberately *inside* a
function: ``integrity`` imports this module at its own bottom, and a
module-level import back would make the pair order-dependent — importing
``integrity_policy`` first would half-initialise ``integrity``. Lazy imports
make the cycle impossible rather than merely unlikely.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: What a signature produced against each superseded surface leaves
#: unprotected — surfaced verbatim in the migration warning so an operator can
#: judge the residual risk without reading either module. Keyed by the plain
#: integer value of ``HashSurface`` so this module needs no import-time
#: dependency on ``integrity`` (see the module docstring).
_SURFACE_GAPS: dict[int, str] = {
    1: (
        "build and packaging files (pyproject.toml, requirements*.txt, ...), "
        "SKILL.md prompt bodies, native extension modules, shipped front-end "
        "assets (ui/dist, static: JS/HTML/CSS) and the manifest"
    ),
    2: (
        "native extension modules (*.so/*.pyd/*.dylib), shell scripts, shipped "
        "front-end assets (ui/dist, static: JS/HTML/CSS) and the manifest — "
        "code that runs on the host or in the operator's browser, and the file "
        "that says what it may reach"
    ),
    3: (
        "front-end bundles built outside ui/dist (ui/out from a Next.js static "
        "export, ui/build from Create React App) and the manifest — a console "
        "that ships and runs in the operator's browser with nothing attesting "
        "its bytes, and the file that grants the plugin its permissions"
    ),
    4: (
        "the manifest — its declared permissions (network egress, tool and "
        "secret grants), python_dependencies, min_core_version and name. "
        "Anyone who can edit manifest.yaml can widen this plugin's egress "
        "without breaking its hash or its publisher signature"
    ),
}


def surface_gap(surface: int) -> str:
    """What a signature computed over ``surface`` leaves unprotected today.

    Args:
        surface: A superseded surface generation (a ``HashSurface`` value).

    Returns:
        Human-readable prose for the migration warning, or a generic fallback
        for a surface with no recorded gap.
    """
    return _SURFACE_GAPS.get(int(surface), "part of the plugin's shipped surface")


def is_strict_mode_enabled() -> bool:
    """Return True when ``BASELITH_REQUIRE_SIGNED_PLUGINS`` is set to a truthy value."""
    raw = os.environ.get("BASELITH_REQUIRE_SIGNED_PLUGINS", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _is_production() -> bool:
    """Whether the runtime environment is production.

    Delegates to :mod:`core.utils.runtime_env`, which is stdlib-only and so
    keeps this module free of the pydantic/config import the lightweight-CI
    constraint rules out. The hand-rolled copy this replaced matched the
    literal ``"production"`` only, so ``APP_ENV=prod`` silently disabled the
    signing gate below.
    """
    from core.utils.runtime_env import is_production_env

    return is_production_env()


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


def _match_legacy_surface(plugin_dir: Path, expected_hash: str) -> int | None:
    """Return the superseded surface ``expected_hash`` was computed over, if any."""
    from .integrity import _LEGACY_SURFACES, compute_plugin_hash

    wanted = expected_hash.lower()
    for surface in _LEGACY_SURFACES:
        if compute_plugin_hash(plugin_dir, surface=surface).lower() == wanted:
            return surface
    return None


def _handle_legacy_match(surface: int, safe_name: str, *, strict: bool) -> bool:
    """Log and decide on a signature that only matches a superseded surface."""
    from .integrity import CURRENT_HASH_SURFACE, HashSurface

    name = HashSurface(surface).name
    if strict:
        logger.error(
            "Refusing plugin %s: integrity_sha256 matches only the superseded "
            "hash surface %s, but BASELITH_REQUIRE_SIGNED_PLUGINS demands %s. "
            "Re-sign the plugin.",
            safe_name,
            name,
            CURRENT_HASH_SURFACE.name,
        )
        return False
    logger.warning(
        "Plugin %s is signed against the superseded hash surface %s: %s are "
        "NOT covered by its signature. Re-sign the plugin to extend coverage.",
        safe_name,
        name,
        surface_gap(surface),
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
    from .integrity import compute_plugin_hash

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


__all__ = [
    "enforce_signing_policy",
    "is_skip_check_enabled",
    "is_strict_mode_enabled",
    "surface_gap",
    "verify_plugin_integrity",
]
