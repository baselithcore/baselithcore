"""Startup posture checks for :class:`core.config.security.SecurityConfig`.

Split out of ``security.py`` for the module size cap, on the same seam as
:mod:`core.config._security_parsers`: the settings class keeps its declarations
and the cross-field derivations that need the whole model, while the checks
that merely *read* a finished model and decide whether to refuse or warn live
here — pure, and testable without building a settings object from an
environment.

The split between raising and warning is deliberate and load-bearing. A missing
or too-short ``SECRET_KEY``, a default admin password, or a wildcard CORS origin
alongside admin credentials are refusals: each one makes the deployment
exploitable in a way no operator would choose knowingly. A short API key is a
warning: existing deployments and test fixtures carry them, and locking them out
at import time is worse than telling the operator to rotate.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config.security import SecurityConfig

logger = logging.getLogger(__name__)

#: Minimum length for an API key, below which the SHA-256 hashing in
#: ``core/auth/api_keys.py`` stops being sound (it assumes a high-entropy
#: random token, not a memorable string).
MIN_API_ENTROPY_CHARS = 32


def check_insecure_defaults(config: SecurityConfig) -> None:
    """Refuse or warn about dangerous security configuration.

    Args:
        config: A fully-built :class:`~core.config.security.SecurityConfig`.

    Raises:
        ValueError: The configuration is exploitable as written — see the
            module docstring for which cases refuse rather than warn.
    """
    if config.auth_required and not config.secret_key:
        raise ValueError(
            "SECRET_KEY is required when AUTH_REQUIRED=true. "
            'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
        )
    if config.secret_key and len(config.secret_key.get_secret_value()) < 32:
        raise ValueError("SECRET_KEY is too short. Minimum length is 32 characters.")
    if config.admin_pass and config.admin_pass.get_secret_value() in (
        "password",
        "changeme",
        "admin",
    ):
        raise ValueError(
            "SECURITY: ADMIN_PASS is set to an insecure default ('password', 'changeme', or 'admin'). "
            "Change it before deploying to production."
        )
    # The API-key path hashes with SHA-256 rather than a password KDF
    # (core/auth/api_keys.py), which is only sound while the keys are
    # high-entropy random tokens. Nothing enforced that premise, so a
    # hand-typed short key silently got password-grade treatment from a
    # fast hash. Warn rather than raise: existing deployments (and tests)
    # carry short keys, and locking them out at import time is worse than
    # telling the operator to rotate. Only the minimum length is logged —
    # never a count or any value derived from the keys themselves.
    has_short_key = any(
        len(key.get_secret_value()) < MIN_API_ENTROPY_CHARS
        for key in (
            *config.api_keys_user,
            *config.api_keys_admin,
            *config.api_keys_job,
            *config.api_keys_scoped,
        )
    )
    if has_short_key:
        logger.warning(
            "SECURITY: at least one configured API key is shorter than %d "
            "characters. API keys are hashed with SHA-256 (fast, correct for "
            "random tokens) — a short or guessable key is brute-forceable. "
            'Mint keys with: python -c "import secrets; '
            'print(secrets.token_urlsafe(32))"',
            MIN_API_ENTROPY_CHARS,
        )

    if "*" in config.allow_origins:
        if config.admin_pass or config.admin_pass_hashed:
            # Wildcard + admin credentials (plain or hashed) is a critical
            # vulnerability: the CSRF Origin check becomes a no-op under
            # wildcard, while browsers replay cached Basic-auth credentials
            # on cross-site form POSTs against the admin router/console.
            raise ValueError(
                "SECURITY CRITICAL: 'ALLOW_ORIGINS' contains '*' (wildcard) while "
                "'ADMIN_PASS' or 'ADMIN_PASS_HASHED' is set. "
                "Cross-origin credentialed requests (CORS) are disabled for wildcards, which will "
                "break the Admin Console. You MUST explicitly list allowed origins or use a specific domain "
                "for production."
            )
        logger.warning(
            "SECURITY: 'ALLOW_ORIGINS' contains '*' (wildcard). "
            "Cross-origin requests will be allowed from ANY site, but credentials (cookies/auth) "
            "will be disabled by the framework for security."
        )


__all__ = ["MIN_API_ENTROPY_CHARS", "check_insecure_defaults"]
