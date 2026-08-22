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
