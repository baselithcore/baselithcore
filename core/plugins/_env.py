"""Plugin ``.env`` loading with framework-security key filtering.

Split out of :mod:`core.plugins.loader` to keep that module under the file-size
cap. A plugin's ``.env`` is deliberately outside the integrity-hashed surface
(operators supply per-deployment secrets without re-signing), so it must never
be able to flip process-wide security controls — see :func:`is_protected_env_key`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Framework-global controls a plugin's own ``.env`` must never set. A tampered
# or even a legitimately-signed plugin ``.env`` could otherwise flip process-wide
# security — e.g. ``BASELITH_SANITIZE_EXTERNAL_CONTENT=false`` (disables
# indirect-prompt-injection scrubbing), ``BASELITH_BROWSER_ALLOW_INTERNAL=true``
# or ``MCP_ALLOW_INTERNAL_ENDPOINTS=true`` (defeats SSRF guards), or
# ``BASELITH_REQUIRE_SIGNED_PLUGINS=false``. Keys matching these are stripped
# before the ``.env`` touches the process environment; a plugin may still set
# its own plugin-scoped variables.
_PROTECTED_ENV_PREFIXES = ("BASELITH_", "MCP_")
_PROTECTED_ENV_KEYS = frozenset(
    {
        "SECRET_KEY",
        "APP_BASE_URL",
        "APP_ENV",
        "ENVIRONMENT",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
    }
)


def is_protected_env_key(key: str) -> bool:
    """Whether ``key`` is a framework-global control a plugin ``.env`` may not set."""
    upper = key.upper()
    if upper in _PROTECTED_ENV_KEYS:
        return True
    return any(upper.startswith(prefix) for prefix in _PROTECTED_ENV_PREFIXES)


def apply_plugin_env(
    plugin_env: Path, plugin_name: str, config: dict[str, object]
) -> None:
    """Load a plugin ``.env`` into the process env and plugin ``config``.

    Framework-protected keys (see :func:`is_protected_env_key`) are dropped so a
    plugin ``.env`` can only set its own plugin-scoped variables — it can never
    weaken process-wide security, even though ``.env`` sits outside the
    integrity-hashed surface. Existing process/config values are never clobbered
    (``override=False`` semantics).
    """
    env_vars = dotenv_values(plugin_env)
    # Prefer existing configs over .env defaults if already defined. Precompute
    # the lowered key set once instead of rebuilding it per env var.
    config_keys_lower = {ck.lower() for ck in config.keys()}
    blocked: list[str] = []
    for k, v in env_vars.items():
        if not k or v is None:
            continue
        if is_protected_env_key(k):
            blocked.append(k)
            continue
        # override=False: never clobber a variable already in the environment.
        os.environ.setdefault(k, v)
        k_lower = k.lower()
        if k_lower not in config_keys_lower:
            config[k_lower] = v
            config_keys_lower.add(k_lower)
    if blocked:
        logger.warning(
            "Plugin %s .env attempted to set framework-protected keys %s; ignored.",
            plugin_name,
            sorted(blocked),
        )
    logger.debug("Loaded plugin environment file: %s", plugin_env)
