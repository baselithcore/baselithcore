"""The plugin-``.env`` protected-key list must cover every process-wide control.

A plugin ``.env`` sits outside the integrity-hashed surface, so any key it can
set is a key an attacker who tampers with an installed plugin directory can
set. Beyond the framework's own ``BASELITH_*``/``MCP_*`` namespaces, that means
the Python-ecosystem egress/TLS knobs (proxy vars, CA-bundle overrides) and
every auth/config toggle read from the environment.
"""

from __future__ import annotations

import pytest

from core.plugins._env import (
    EnvKeyVerdict,
    classify_plugin_env_key,
    is_protected_env_key,
    namespace_prefix,
    namespace_prefixes,
)


@pytest.mark.parametrize(
    "key",
    [
        # Egress redirection: routes every outbound request through an
        # attacker-chosen proxy (httpx/requests both honor these).
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        # TLS trust override: a rogue CA bundle turns MITM into a config change.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        # Auth / exposure toggles.
        "AUTH_REQUIRED",
        "ALLOW_ORIGINS",
        "TRUSTED_HOSTS",
        "DOCS_ENABLED",
        "ADMIN_USER",
        "ADMIN_PASS",
        "API_KEYS_ADMIN",
        "API_KEYS_USER",
        # Token/crypto material.
        "JWT_ALGORITHM",
        "JWT_KEYS",
        "JWT_ACTIVE_KID",
        "JWT_SIGNING_KEY",
        "DATA_ENCRYPTION_KEYS",
        # Backing-store DSNs/credentials.
        "DATABASE_URL",
        "DB_PASSWORD",
        "REDIS_URL",
        # Existing coverage must not regress.
        "SECRET_KEY",
        "BASELITH_REQUIRE_SIGNED_PLUGINS",
        "MCP_ALLOW_INTERNAL_ENDPOINTS",
        # A2A/webhook SSRF + secrets backend + rate limiter — other framework
        # namespaces a plugin must not flip process-wide.
        "A2A_ALLOW_INTERNAL_ENDPOINTS",
        "WEBHOOK_ALLOW_INTERNAL",
        "SECRETS_BACKEND",
        "SECRETS_DIR",
        "RATE_LIMIT_USER_PER_MINUTE",
        "RATE_LIMIT_FAIL_MODE",
        "CORS_ALLOW_CREDENTIALS",
        "CSRF_PROTECTION_ENABLED",
        # Telemetry / error sinks: *_ENDPOINT / *_DSN exfiltrate to a collector.
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "SENTRY_DSN",
        # HTTP-surface security controls.
        "SECURITY_HEADERS_ENABLED",
        "CONTENT_SECURITY_POLICY",
        "X_FRAME_OPTIONS",
        "MAX_REQUEST_SIZE_BYTES",
        "METRICS_AUTH_REQUIRED",
        "FORWARDED_ALLOW_IPS",
        "PROXY_HEADERS",
        # Interpreter / dynamic-loader hijack (read before framework code runs).
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONHOME",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        # LLM-provider base URLs: repointing exfiltrates every prompt.
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "ANTHROPIC_BASE_URL",
        "OLLAMA_HOST",
        "HF_ENDPOINT",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ],
)
def test_framework_global_keys_are_protected(key: str) -> None:
    assert is_protected_env_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "MYPLUGIN_TOKEN",
        "BASELITHBOT_CHANNEL",
        "FOO",
        # A plugin whose own namespace merely *starts with* a dangerous word
        # must not be blocked: exact-key matching for PYTHON*/LD*/DYLD* vectors
        # keeps these settable.
        "PYTHON_TOOLS_API_KEY",
        "LDAP_PLUGIN_URL",
    ],
)
def test_plugin_scoped_keys_stay_settable(key: str) -> None:
    assert not is_protected_env_key(key)


# --------------------------------------------------------------------------
# Namespace allowlist — the primary gate. The denylist above can only cover
# the process-wide controls someone thought of; these tests pin the closed
# policy that catches the ones nobody listed.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dir_name", "expected"),
    [
        ("baselithbot", "BASELITHBOT_"),
        ("document_sources", "DOCUMENT_SOURCES_"),
        ("document-sources", "DOCUMENT_SOURCES_"),
        ("example-plugin", "EXAMPLE_PLUGIN_"),
        ("--", ""),
    ],
)
def test_namespace_prefix_derivation(dir_name: str, expected: str) -> None:
    assert namespace_prefix(dir_name) == expected


def test_namespace_prefixes_dedupes_and_drops_empty() -> None:
    # Directory name and manifest name usually match: one prefix, not two.
    assert namespace_prefixes("web_scraper", "web-scraper") == ("WEB_SCRAPER_",)
    assert namespace_prefixes("", "goals") == ("GOALS_",)
    assert namespace_prefixes("", "!!") == ()


def test_in_namespace_key_allowed() -> None:
    verdict = classify_plugin_env_key("BASELITHBOT_CHANNEL", prefixes=("BASELITHBOT_",))
    assert verdict is EnvKeyVerdict.ALLOW


@pytest.mark.parametrize(
    "key",
    [
        # Not on any denylist, and none of them is the plugin's to export:
        # this is exactly the class of key a denylist can never enumerate.
        "AWS_SECRET_ACCESS_KEY",
        "GIT_SSH_COMMAND",
        "NODE_OPTIONS",
        "GODEBUG",
        "SOME_FUTURE_LIBRARY_ENDPOINT",
        "PATH",
    ],
)
def test_unlisted_out_of_namespace_keys_are_refused(key: str) -> None:
    """A key nobody put on the denylist is still refused — that is the point."""
    assert not is_protected_env_key(key)
    verdict = classify_plugin_env_key(key, prefixes=("MYPLUGIN_",))
    assert verdict is EnvKeyVerdict.OUT_OF_NAMESPACE


def test_manifest_declaration_widens_the_allowlist() -> None:
    """The documented migration path for a legitimately un-namespaced key."""
    verdict = classify_plugin_env_key(
        "SLACK_SIGNING_SECRET",
        prefixes=("MYPLUGIN_",),
        declared_keys=frozenset({"SLACK_SIGNING_SECRET"}),
    )
    assert verdict is EnvKeyVerdict.ALLOW


def test_manifest_declaration_cannot_reopen_a_protected_key() -> None:
    """The denylist runs first, so a declaration only ever widens to safe keys."""
    for key in ("HTTPS_PROXY", "BASELITH_REQUIRE_SIGNED_PLUGINS", "PYTHONPATH"):
        verdict = classify_plugin_env_key(
            key, prefixes=("MYPLUGIN_",), declared_keys=frozenset({key})
        )
        assert verdict is EnvKeyVerdict.PROTECTED


def test_namespace_shadowing_a_framework_prefix_cannot_reopen_it() -> None:
    """A plugin dir named ``baselith-x`` derives ``BASELITH_X_`` — still denied."""
    prefixes = namespace_prefixes("baselith-x")
    assert prefixes == ("BASELITH_X_",)
    verdict = classify_plugin_env_key("BASELITH_X_TOKEN", prefixes=prefixes)
    assert verdict is EnvKeyVerdict.PROTECTED
