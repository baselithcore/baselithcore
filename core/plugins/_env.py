"""Plugin ``.env`` loading policy — namespace allowlist + protected-key denylist.

Split out of :mod:`core.plugins.loader` to keep that module under the file-size
cap. A plugin's ``.env`` is deliberately outside the integrity-hashed surface
(operators supply per-deployment secrets without re-signing), so it must never
be able to flip process-wide security controls.

Two gates, in this order — and this module is the single source of truth for
both, shared with the public :mod:`core.plugins.env` helper so the loader path
and the plugin-self-service path can never drift apart again:

1. **Denylist** (:func:`is_protected_env_key`) — framework-global controls are
   refused unconditionally, whatever the plugin claims to own. A denylist is
   incomplete by construction (it protects only what someone remembered to
   list), so it is the *second* line of defence here, not the only one.
2. **Namespace allowlist** (:func:`classify_plugin_env_key`) — a plugin may
   only export its own ``<PLUGIN>_``-prefixed keys to the process environment.
   That is a closed policy: an unforeseen dangerous key that nobody put on the
   denylist is still refused, because it is not in the plugin's namespace.

Why the allowlist is the primary gate: the denylist can only ever describe the
*known* process-wide controls of the framework, of CPython, and of every
third-party library in the venv. The set of environment variables a library
reads is unbounded and changes with every dependency bump, so "block the bad
ones" loses that race by design. "Only your own namespace leaves the plugin"
does not.
"""

from __future__ import annotations

import os
import re
from enum import Enum
from pathlib import Path

from dotenv import dotenv_values

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Temporary, documented opt-out restoring the pre-namespace behaviour (denylist
# only) for a deployment whose plugins still publish out-of-namespace keys to
# ``os.environ`` and cannot be updated yet. Deprecated on introduction: the
# refusal warnings name every key that has to move, and this escape hatch is
# scheduled for removal once those are migrated. Protected keys stay refused
# even with the flag on — it widens the allowlist, it never disables the
# denylist.
LEGACY_DENYLIST_ONLY_FLAG = "BASELITH_PLUGIN_ENV_LEGACY_DENYLIST"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Framework-global controls a plugin's own ``.env`` must never set. A tampered
# or even a legitimately-signed plugin ``.env`` could otherwise flip process-wide
# security — e.g. ``BASELITH_SANITIZE_EXTERNAL_CONTENT=false`` (disables
# indirect-prompt-injection scrubbing), ``BASELITH_BROWSER_ALLOW_INTERNAL=true``
# or ``MCP_ALLOW_INTERNAL_ENDPOINTS=true`` (defeats SSRF guards), or
# ``BASELITH_REQUIRE_SIGNED_PLUGINS=false``. Keys matching these are stripped
# before the ``.env`` touches the process environment; a plugin may still set
# its own plugin-scoped variables.
#
# Beyond the framework's own namespaces this must also cover what the Python
# ecosystem itself reads from the environment: the proxy variables reroute
# every outbound httpx/requests call through an attacker-chosen host, and the
# CA-bundle overrides turn TLS interception into a config change. Both are
# honored process-wide by libraries the framework does not control.
_PROTECTED_ENV_PREFIXES = (
    "BASELITH_",
    "MCP_",
    "JWT_",
    "API_KEYS_",
    "ADMIN_",
    "OIDC_",
    "DB_",
    "REDIS_",
    # Other framework namespaces a plugin must not flip process-wide: A2A/
    # webhook SSRF and auth toggles, the secrets backend, the rate limiter, and
    # telemetry/error sinks (whose *_ENDPOINT/*_DSN reroute traces and errors to
    # an attacker-chosen collector).
    "A2A_",
    "WEBHOOK_",
    "SECRETS_",
    "RATE_LIMIT_",
    "CORS_",
    "CSRF_",
    "OTEL_",
    "SENTRY_",
)
_PROTECTED_ENV_KEYS = frozenset(
    {
        "SECRET_KEY",
        "APP_BASE_URL",
        "APP_ENV",
        "ENVIRONMENT",
        # Auth / exposure toggles.
        "AUTH_REQUIRED",
        "ALLOW_ORIGINS",
        "TRUSTED_HOSTS",
        "DOCS_ENABLED",
        "DATA_ENCRYPTION_KEYS",
        "DATABASE_URL",
        # HTTP-surface security controls read at startup / per request.
        "SECURITY_HEADERS_ENABLED",
        "CONTENT_SECURITY_POLICY",
        "X_FRAME_OPTIONS",
        "MAX_REQUEST_SIZE_BYTES",
        "METRICS_AUTH_REQUIRED",
        "FORWARDED_ALLOW_IPS",
        "PROXY_HEADERS",
        # Egress redirection honored by httpx/requests/urllib.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        # TLS trust-store overrides honored by ssl/requests/curl bindings.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        # LLM-provider base-URL overrides: repointing these exfiltrates every
        # prompt (and any tool output) to an attacker-controlled endpoint.
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_URL",
        "OLLAMA_HOST",
        "OLLAMA_BASE_URL",
        "HF_ENDPOINT",
        "HUGGINGFACE_ENDPOINT",
        "GEMINI_BASE_URL",
        "GOOGLE_API_BASE",
        "COHERE_BASE_URL",
        "GOOGLE_APPLICATION_CREDENTIALS",
        # Interpreter / dynamic-loader hijack. CPython and the OS loader read
        # these *before* any framework code runs, so a plugin .env that set them
        # would divert imports or preload an attacker library process-wide.
        # Listed as exact keys (not a "PYTHON"/"LD_" prefix) so a plugin's own
        # namespaced keys — e.g. PYTHON_TOOLS_API_KEY — are not caught.
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONEXECUTABLE",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FRAMEWORK_PATH",
    }
)


def is_protected_env_key(key: str) -> bool:
    """Whether ``key`` is a framework-global control a plugin ``.env`` may not set."""
    upper = key.upper()
    if upper in _PROTECTED_ENV_KEYS:
        return True
    return any(upper.startswith(prefix) for prefix in _PROTECTED_ENV_PREFIXES)


def namespace_prefix(plugin_dir_name: str) -> str:
    """Derive the ``<PLUGIN>_`` env-key namespace from a plugin directory name.

    ``document-sources`` → ``DOCUMENT_SOURCES_``. Returns ``""`` when the name
    has no alphanumeric content (callers treat that as "no derivable namespace").
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "_", plugin_dir_name).strip("_").upper()
    return f"{slug}_" if slug else ""


def namespace_prefixes(*names: str) -> tuple[str, ...]:
    """Derive the ordered, de-duplicated namespace prefixes for ``names``.

    The loader passes both the plugin *directory* name and the manifest *name*.
    Docs require the two to be equal, but tolerating a mismatch here costs
    nothing: every derived prefix still has to clear the denylist before any
    key using it is accepted.
    """
    seen: dict[str, None] = {}
    for name in names:
        prefix = namespace_prefix(name)
        if prefix:
            seen.setdefault(prefix, None)
    return tuple(seen)


class EnvKeyVerdict(Enum):
    """Outcome of applying the plugin ``.env`` policy to one key."""

    ALLOW = "allow"
    """In the plugin's own namespace (or explicitly declared) and not protected."""

    PROTECTED = "protected"
    """A framework-global control — refused unconditionally."""

    OUT_OF_NAMESPACE = "out_of_namespace"
    """Not protected, but not the plugin's to export process-wide either."""


def classify_plugin_env_key(
    key: str,
    *,
    prefixes: tuple[str, ...],
    declared_keys: frozenset[str] = frozenset(),
) -> EnvKeyVerdict:
    """Decide whether a plugin ``.env`` key may reach ``os.environ``.

    Args:
        key: The raw key as written in the ``.env``.
        prefixes: Namespace prefixes the plugin owns (upper-case, trailing ``_``).
        declared_keys: Exact upper-case keys the plugin's manifest declares in
            ``environment_variables``. This is the documented migration path for
            a legitimately un-namespaced key (``SLACK_SIGNING_SECRET``,
            ``DISCORD_PUBLIC_KEY``, …): the publisher names it once in the
            manifest instead of the operator's ``.env`` smuggling it in. A
            declaration can only ever widen the allowlist to *non-protected*
            keys — the denylist below is checked first — so the worst a tampered
            manifest buys is the ability to set a key that the plugin's own
            already-executing code could have set with ``os.environ[...] = ...``
            anyway. No privilege is gained.
    """
    upper = key.upper()
    # Denylist first, so neither a namespace that happens to shadow a framework
    # prefix nor a manifest declaration can re-open a process-wide control.
    if is_protected_env_key(upper):
        return EnvKeyVerdict.PROTECTED
    if upper in declared_keys:
        return EnvKeyVerdict.ALLOW
    if prefixes and upper.startswith(prefixes):
        return EnvKeyVerdict.ALLOW
    return EnvKeyVerdict.OUT_OF_NAMESPACE


def _legacy_denylist_only() -> bool:
    """Whether the deprecated denylist-only opt-out is enabled."""
    return os.getenv(LEGACY_DENYLIST_ONLY_FLAG, "").strip().lower() in _TRUTHY


def apply_plugin_env(
    plugin_env: Path,
    plugin_name: str,
    config: dict[str, object],
    *,
    plugin_dir_name: str | None = None,
    declared_env_keys: tuple[str, ...] = (),
) -> None:
    """Load a plugin ``.env`` into the process env and plugin ``config``.

    Policy (see the module docstring for why):

    - **Protected keys are dropped entirely** — not exported, not merged into
      ``config``. A plugin ``.env`` can never weaken process-wide security, even
      though ``.env`` sits outside the integrity-hashed surface.
    - **Out-of-namespace keys are not exported to ``os.environ``**, but they
      *are* still merged into this plugin's own ``config`` dict. That dict is
      handed to exactly one plugin's ``initialize()``, so it is not a
      process-global surface; keeping the merge preserves the historically
      documented behaviour for the common case (a plugin reading its settings
      from ``config``) while closing the part that actually leaks — writing an
      arbitrary name into the environment every library in the venv can read.
    - **In-namespace keys behave as before**: exported and merged.

    Existing process/config values are never clobbered (``override=False``).

    Args:
        plugin_env: Path to the plugin's ``.env``.
        plugin_name: Manifest name, used for logging and namespace derivation.
        config: The plugin's config dict, merged in place.
        plugin_dir_name: Plugin directory name; defaults to the ``.env``'s parent
            directory. Contributes the primary ``<DIRNAME>_`` namespace.
        declared_env_keys: ``environment_variables`` from the manifest — exact
            keys the publisher declares this plugin needs. See
            :func:`classify_plugin_env_key`.
    """
    # Containment, mirroring core.plugins.env: the file must resolve to a real
    # file directly inside the plugin dir, never a link to host secrets.
    plugin_dir = plugin_env.parent
    try:
        if (
            plugin_env.is_symlink()
            or plugin_env.resolve().parent != plugin_dir.resolve()
        ):
            logger.warning(
                "Plugin %s .env escapes its plugin dir; ignored.", plugin_name
            )
            return
    except OSError:
        logger.warning("Plugin %s .env is unreadable; ignored.", plugin_name)
        return

    prefixes = namespace_prefixes(plugin_dir_name or plugin_dir.name, plugin_name)
    declared = frozenset(k.upper() for k in declared_env_keys if k)
    legacy_mode = _legacy_denylist_only()

    env_vars = dotenv_values(plugin_env)
    # Prefer existing configs over .env defaults if already defined. Precompute
    # the lowered key set once instead of rebuilding it per env var.
    config_keys_lower = {ck.lower() for ck in config.keys()}
    blocked: list[str] = []
    out_of_namespace: list[str] = []
    for k, v in env_vars.items():
        if not k or v is None:
            continue
        verdict = classify_plugin_env_key(k, prefixes=prefixes, declared_keys=declared)
        if verdict is EnvKeyVerdict.PROTECTED:
            blocked.append(k)
            continue
        if verdict is EnvKeyVerdict.OUT_OF_NAMESPACE:
            out_of_namespace.append(k)
            if not legacy_mode:
                # Plugin-local config only — never the process environment.
                _merge_config(config, config_keys_lower, k, v)
                continue
        # override=False: never clobber a variable already in the environment.
        os.environ.setdefault(k, v)
        _merge_config(config, config_keys_lower, k, v)

    if blocked:
        logger.warning(
            "Plugin %s .env attempted to set framework-protected keys %s; ignored.",
            plugin_name,
            sorted(blocked),
        )
    if out_of_namespace:
        logger.warning(
            "Plugin %s .env has %d out-of-namespace key(s) %s (namespace: %s). "
            "%s Rename them to the plugin namespace, or declare the exact keys in "
            "the manifest 'environment_variables' list.",
            plugin_name,
            len(out_of_namespace),
            sorted(out_of_namespace),
            ", ".join(prefixes) or "<none>",
            (
                f"{LEGACY_DENYLIST_ONLY_FLAG} is set, so they were still exported "
                "to os.environ; this opt-out is deprecated and will be removed."
                if legacy_mode
                else "They were merged into the plugin config but NOT exported to "
                "os.environ."
            ),
        )
    logger.debug("Loaded plugin environment file: %s", plugin_env)


def _merge_config(
    config: dict[str, object], config_keys_lower: set[str], key: str, value: str
) -> None:
    """Merge one ``.env`` entry into ``config`` without clobbering existing keys."""
    k_lower = key.lower()
    if k_lower not in config_keys_lower:
        config[k_lower] = value
        config_keys_lower.add(k_lower)
