"""
Security configuration.

Authentication, Security Headers, and Rate Limiting.
"""

import logging
import os
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.config._collections import csv_list
from core.config._security_parsers import (
    coerce_to_secret_set,
    parse_algorithms,
    parse_encryption_keys,
    parse_role_map,
    parse_scoped_keys,
)
from core.config._security_posture import check_insecure_defaults

logger = logging.getLogger(__name__)


class SecurityConfig(BaseSettings):
    """
    Security configuration.
    """

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    # === Auth & Secrets ===
    secret_key: SecretStr | None = Field(default=None, alias="SECRET_KEY")
    auth_required: bool = Field(default=True, alias="AUTH_REQUIRED")
    jwt_issuer: str | None = Field(default=None, alias="JWT_ISSUER")
    jwt_audience: str | None = Field(default=None, alias="JWT_AUDIENCE")
    jwt_strict_validation: bool = Field(
        default=False,
        alias="JWT_STRICT_VALIDATION",
        description="When true, reject JWTs missing aud/iss claims (recommended for multi-region deployments).",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        alias="JWT_ALGORITHM",
        description=(
            "JWS algorithm for access/refresh tokens. HS256 (default) signs and "
            "verifies with SECRET_KEY. An asymmetric choice (EdDSA/RS256/ES256) "
            "additionally needs JWT_SIGNING_KEY (private) and JWT_KEYS (public), "
            "and lets a service verify tokens without being able to mint them."
        ),
    )
    jwt_keys: SecretStr | None = Field(
        default=None,
        alias="JWT_KEYS",
        description=(
            "Verification key ring as 'kid1=key1,kid2=key2'. Accepting several "
            "keys at once is what makes rotation non-disruptive: add the new "
            "key, point JWT_ACTIVE_KID at it, and drop the old one after the "
            "longest token lifetime has elapsed — no session is ever "
            "invalidated. SecretStr: with HS256 (the default) every entry is a "
            "signing-capable shared secret, so it must never surface in "
            "repr()/dumps/Sentry frames."
        ),
    )
    jwt_active_kid: str | None = Field(
        default=None,
        alias="JWT_ACTIVE_KID",
        description="Which JWT_KEYS entry signs new tokens (required if it lists more than one).",
    )
    jwt_signing_key: SecretStr | None = Field(
        default=None,
        alias="JWT_SIGNING_KEY",
        description="Private key for asymmetric signing. Omit on verify-only services.",
    )
    # Access-token lifetime in seconds. A short access TTL is the primary
    # compensating control for stateless JWTs (RFC 9700 §2.1). Accepts the
    # historical ``AUTH_SESSION_LIFETIME`` alias so operators setting the
    # session lifetime actually shorten the issued token, not just the
    # advertised ``expires_in``.
    access_token_lifetime: int = Field(
        default=3600,
        validation_alias=AliasChoices(
            "AUTH_ACCESS_TOKEN_LIFETIME", "AUTH_SESSION_LIFETIME"
        ),
        ge=60,
        description=(
            "Access-token lifetime in seconds (default 1h; legacy alias "
            "AUTH_SESSION_LIFETIME). Refresh tokens keep the JWTHandler default "
            "of 7 days."
        ),
    )
    api_key_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("API_KEY_ENABLED", "SECURITY_API_KEY_ENABLED"),
        description=(
            "Master switch for API-key authentication. When false, API keys are "
            "rejected entirely."
        ),
    )
    # Behaviour when the shared revocation denylist (Redis) cannot be read:
    #   closed — treat the key as revoked, i.e. reject the request. A revoked
    #            key stays revoked even while the denylist is unreachable.
    #   open   — trust process-local state, i.e. accept the key. Availability
    #            over revocation: an outage cannot lock out API clients, but it
    #            also un-revokes every key revoked elsewhere for its duration.
    # ``closed`` is the default because revocation is a security control and an
    # attacker who can make Redis unreachable must not thereby restore a key
    # that was taken away from them.
    api_key_revocation_fail_mode: Literal["closed", "open"] = Field(
        default="closed",
        alias="API_KEY_REVOCATION_FAIL_MODE",
        description=(
            "What to do when the shared API-key revocation denylist is "
            "unreadable: 'closed' (default) rejects the key, 'open' accepts it "
            "on process-local state."
        ),
    )

    # === Multi-factor authentication (TOTP / RFC 6238) ===
    # Opt-in second factor (NIS2 Art. 21(2)(j)). When enabled, applications can
    # enroll users via AuthManager.mfa and require a TOTP step-up at login.
    # Disabled by default — purely additive, no effect on existing auth paths.
    mfa_enabled: bool = Field(default=False, alias="MFA_ENABLED")
    # Issuer label shown in the user's authenticator app (Google Authenticator,
    # Authy, …) — typically the product or tenant name.
    mfa_issuer: str = Field(default="BaselithCore", alias="MFA_ISSUER")

    # === Federated SSO / OIDC ===
    # When enabled, bearer tokens that are not local HS256 tokens are verified
    # against an external OpenID Connect provider (Okta/Auth0/Azure AD/Keycloak)
    # by fetching its JWKS and validating the RS256/ES256 signature. Opt-in and
    # additive — local JWT/API-key auth is unaffected when disabled.
    oidc_enabled: bool = Field(default=False, alias="OIDC_ENABLED")
    oidc_issuer: str | None = Field(default=None, alias="OIDC_ISSUER")
    oidc_audience: str | None = Field(default=None, alias="OIDC_AUDIENCE")
    # Optional explicit JWKS endpoint; if unset it is discovered from
    # ``{issuer}/.well-known/openid-configuration``.
    oidc_jwks_url: str | None = Field(default=None, alias="OIDC_JWKS_URL")
    # ``NoDecode`` on every collection field below: pydantic-settings
    # JSON-decodes complex types inside EnvSettingsSource *before* any
    # validator runs, so the coercers these fields already declare never saw
    # the raw string and a plain ``RS256,ES256`` raised SettingsError out of
    # the whole SecurityConfig — i.e. no API at all.
    oidc_algorithms: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["RS256"], alias="OIDC_ALGORITHMS"
    )
    # Claim names to read identity/authorization from (IdP-specific).
    oidc_username_claim: str = Field(default="sub", alias="OIDC_USERNAME_CLAIM")
    oidc_roles_claim: str = Field(default="roles", alias="OIDC_ROLES_CLAIM")
    oidc_scopes_claim: str = Field(default="scope", alias="OIDC_SCOPES_CLAIM")
    oidc_tenant_claim: str | None = Field(default=None, alias="OIDC_TENANT_CLAIM")
    oidc_default_role: str = Field(default="user", alias="OIDC_DEFAULT_ROLE")
    # Map IdP role strings to BaselithCore AuthRole values:
    # "okta-admins:admin,okta-users:user".
    oidc_role_map: Annotated[dict[str, str], NoDecode] = Field(
        default_factory=dict, alias="OIDC_ROLE_MAP"
    )

    # NoDecode is load-bearing: ``.env.example`` documents this as a plain
    # comma-separated origin list, and pydantic-settings' default list decoder
    # tries to JSON-parse it, so following the template took the entire
    # SecurityConfig down at import time.
    #
    # The description below is explicit rather than inherited from this comment
    # because the generated reference page (``scripts/check_config_surface.py``)
    # renders it into a table cell, where a literal origin URL trips
    # markdownlint's MD034. Keep the prose URL-free.
    allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        alias="ALLOW_ORIGINS",
        description=(
            "Comma-separated list of browser origins allowed to make "
            "cross-origin requests, each written scheme-and-host (no trailing "
            "path). Empty — the default — blocks every cross-origin request, "
            "which is the safe posture for an API with no browser front end."
        ),
    )
    trusted_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        alias="TRUSTED_HOSTS",
        description=(
            "Host allowlist. Empty — the default — leaves TrustedHostMiddleware "
            "unmounted and the Host header unvalidated, so a spoofed Host "
            "poisons absolute URLs built from the request (reset and "
            "verification links) and host-keyed caches. Production logs an "
            "ERROR at startup while this is empty."
        ),
    )

    # API Keys (wrapped in SecretStr to prevent accidental leakage via repr/logs/Sentry)
    api_keys_user: Annotated[set[SecretStr], NoDecode] = Field(
        default_factory=set, alias="API_KEYS_USER"
    )
    api_keys_admin: Annotated[set[SecretStr], NoDecode] = Field(
        default_factory=set, alias="API_KEYS_ADMIN"
    )
    api_keys_job: Annotated[set[SecretStr], NoDecode] = Field(
        default_factory=set, alias="API_KEYS_JOB"
    )

    # Least-privilege scoped API keys: map of key -> set of capability scopes
    # (see core.auth.scopes). Supplied as
    #   "key1=chat:read|chat:write,key2=webhooks:write"
    # — entries comma-separated, key and scope-list split on the first '=', and
    # scopes within a list pipe-separated (scopes themselves contain ':').
    # Keys are SecretStr like every other credential field: a repr()/Sentry
    # capture of the config prints '**********', never the key material.
    # NoDecode: keep pydantic-settings from JSON-decoding the raw env string so
    # the validator below receives it verbatim.
    api_keys_scoped: Annotated[dict[SecretStr, set[str]], NoDecode] = Field(
        default_factory=dict, alias="API_KEYS_SCOPED"
    )

    # Admin Credentials (Legacy/Simple Auth)
    admin_user: str = Field(default="admin", alias="ADMIN_USER")
    metrics_auth_required: bool = Field(
        default=True,
        alias="METRICS_AUTH_REQUIRED",
        description=(
            "Require admin basic auth on GET /metrics. Disable only when the "
            "endpoint is reachable solely from the scrape network (e.g. "
            "restricted by NetworkPolicy) or the scraper sends credentials."
        ),
    )
    admin_pass: SecretStr | None = Field(default=None, alias="ADMIN_PASS")
    admin_pass_hashed: SecretStr | None = Field(default=None, alias="ADMIN_PASS_HASHED")

    # === Secrets backend (resolution of credentials) ===
    # 'env' (default, current behaviour) or 'file' (Docker/K8s mounted secrets),
    # plus any backend registered via core.security.secrets.register_secrets_provider.
    secrets_backend: str = Field(default="env", alias="SECRETS_BACKEND")
    secrets_dir: str | None = Field(default=None, alias="SECRETS_DIR")

    # === Encryption at rest ===
    # Mapping of key_id -> secret material (raw base64 32-byte key or passphrase),
    # supplied as "id1:secret1,id2:secret2"; a value without ':' is loaded under
    # the id 'default'. Empty (the default) disables application-level encryption.
    # NoDecode: skip pydantic-settings' JSON decoding so the raw "id:secret,..."
    # string reaches the field validator below (env source would otherwise try
    # json.loads on it and fail).
    data_encryption_keys: Annotated[dict[str, SecretStr], NoDecode] = Field(
        default_factory=dict, alias="DATA_ENCRYPTION_KEYS"
    )
    data_encryption_active_key_id: str | None = Field(
        default=None, alias="DATA_ENCRYPTION_ACTIVE_KEY_ID"
    )

    # === Rate Limiting ===
    rate_limit_user_per_minute: int | None = Field(
        default=60, alias="RATE_LIMIT_USER_PER_MINUTE"
    )
    # Non-null default so admin endpoints are never silently unlimited: a value
    # of None makes the rate limiter no-op for that scope (unthrottled brute
    # force / resource abuse). 120/min is generous for console use while still
    # bounding abuse. Set explicitly (or to a high number) to widen it.
    rate_limit_admin_per_minute: int | None = Field(
        default=120, alias="RATE_LIMIT_ADMIN_PER_MINUTE"
    )
    rate_limit_job_per_minute: int | None = Field(
        default=None, alias="RATE_LIMIT_JOB_PER_MINUTE"
    )
    # Behavior when the Redis limiter backend is unreachable:
    #   open   — degrade to a per-process in-memory window (N replicas
    #            silently allow up to N x the limit until Redis recovers)
    #   closed — reject rate-limited requests with 503 (availability traded
    #            for a hard limit; the limit is treated as a security control)
    # Unset (the default) resolves at limiter construction: ``closed`` in
    # production when a Redis cache backend is declared (CACHE_BACKEND=redis),
    # where the per-role limits, the auth-failure throttle and the admin
    # lockout are brute-force/cost controls that must not silently widen when
    # the shared counter blips; ``open`` outside production and in deployments
    # that never configured Redis (the per-process window is the design
    # there, not a degraded state). Set explicitly to pin either behaviour.
    rate_limit_fail_mode: str | None = Field(
        default=None, alias="RATE_LIMIT_FAIL_MODE", pattern="^(open|closed)$"
    )
    rate_limit_window_seconds: int = Field(
        default=60, alias="RATE_LIMIT_WINDOW_SECONDS", ge=1
    )
    # Per-source-IP budget for *failed* authentication attempts within the
    # rate-limit window. Unlike the per-role limits above (which meter only
    # already-authenticated traffic), this throttles credential brute-force /
    # stuffing on every ``require_*`` route: once an IP exceeds this many
    # rejected auth attempts it receives 429 instead of an unmetered stream of
    # 401s. Successful auth never touches this counter, so a generous default
    # does not penalise a mistyped token or a NAT'd client. Set to None to
    # disable (not recommended — leaves authenticated routes brute-forceable).
    auth_failure_limit_per_minute: int | None = Field(
        default=20, alias="AUTH_FAILURE_LIMIT_PER_MINUTE"
    )

    # === Security Headers ===
    security_headers_enabled: bool = Field(
        default=True, alias="SECURITY_HEADERS_ENABLED"
    )
    content_security_policy: str | None = Field(
        default=None, alias="CONTENT_SECURITY_POLICY"
    )
    enable_hsts: bool = Field(default=True, alias="ENABLE_HSTS")
    hsts_max_age: int = Field(default=31536000, alias="HSTS_MAX_AGE")
    frame_options: str = Field(default="DENY", alias="X_FRAME_OPTIONS")
    # Restrictive default: deny access to powerful browser features the API/
    # console never needs, so a compromised/embedded page cannot request them.
    # Override with a policy string, or "" to omit the header entirely.
    permissions_policy: str | None = Field(
        default=(
            "geolocation=(), camera=(), microphone=(), payment=(), "
            "usb=(), magnetometer=(), gyroscope=(), accelerometer=()"
        ),
        alias="PERMISSIONS_POLICY",
    )
    # Cross-origin isolation pair (OWASP Secure Headers). COOP severs the
    # window.opener link with cross-origin windows; "same-origin-allow-popups"
    # keeps OAuth/SSO popups opened by the console working. CORP blocks no-cors
    # subresource loads (<img>, <script>) of our responses from foreign origins
    # (CORS-approved fetches from ALLOW_ORIGINS are exempt); "" omits either.
    cross_origin_opener_policy: str | None = Field(
        default="same-origin-allow-popups", alias="CROSS_ORIGIN_OPENER_POLICY"
    )
    cross_origin_resource_policy: str | None = Field(
        default="same-origin", alias="CROSS_ORIGIN_RESOURCE_POLICY"
    )

    # === Request body size limit (bytes) ===
    # Protects against memory-exhaustion DoS from oversized POST/PUT bodies.
    # Default 10 MiB. Set 0 to disable. Multipart uploads >100 MiB should use
    # a dedicated streaming-upload endpoint, not the JSON API.
    max_request_size_bytes: int = Field(
        default=10 * 1024 * 1024,
        alias="MAX_REQUEST_SIZE_BYTES",
        ge=0,
        description="Maximum request body size in bytes. 0 disables the check.",
    )

    @field_validator("api_keys_user", "api_keys_admin", "api_keys_job", mode="before")
    @classmethod
    def _coerce_to_secret_set(cls, v: Any) -> Any:
        """Coerce comma-separated strings or mixed iterables to ``set[SecretStr]``."""
        return coerce_to_secret_set(v)

    @field_validator("oidc_role_map", mode="before")
    @classmethod
    def _parse_role_map(cls, v: Any) -> Any:
        """Parse ``idp_role:app_role`` pairs (comma-separated) into a dict."""
        return parse_role_map(v)

    @field_validator("oidc_algorithms", mode="before")
    @classmethod
    def _parse_algorithms(cls, v: Any) -> Any:
        """Allow a comma-separated string for OIDC_ALGORITHMS."""
        return parse_algorithms(v)

    @field_validator("allow_origins", "trusted_hosts", mode="before")
    @classmethod
    def _parse_origin_lists(cls, v: Any) -> Any:
        """Accept ``a,b`` and a blank value, as well as a JSON array.

        Paired with ``NoDecode`` on both fields. JSON stays supported because
        ``.env.example`` itself ships ``TRUSTED_HOSTS=["app.example.com"]`` and
        deployments configured against the old behaviour have arrays in their
        environment.
        """
        return csv_list(v)

    @field_validator("api_keys_scoped", mode="before")
    @classmethod
    def _parse_scoped_keys(cls, v: Any) -> Any:
        """Parse ``key=scope|scope,...`` into ``dict[SecretStr, set[str]]``."""
        return parse_scoped_keys(v)

    @field_validator("data_encryption_keys", mode="before")
    @classmethod
    def _parse_encryption_keys(cls, v: Any) -> Any:
        """Parse ``kid:secret`` pairs (comma-separated) into ``dict[str, SecretStr]``."""
        return parse_encryption_keys(v)

    @model_validator(mode="after")
    def _validate_encryption_keys(self) -> "SecurityConfig":
        """Validate the active key id resolves against the loaded keys."""
        if self.data_encryption_active_key_id and (
            self.data_encryption_active_key_id not in self.data_encryption_keys
        ):
            raise ValueError(
                "DATA_ENCRYPTION_ACTIVE_KEY_ID "
                f"'{self.data_encryption_active_key_id}' is not present in "
                "DATA_ENCRYPTION_KEYS."
            )
        return self

    @model_validator(mode="after")
    def _derive_jwt_identity(self) -> "SecurityConfig":
        """Give tokens an issuer/audience, and enforce them once auth is on.

        Left unset, ``verify_token`` checks neither claim, so a token minted by
        *any* deployment sharing the secret verifies here — staging tokens
        working in production is the failure this prevents, and it is invisible
        until someone notices.

        The identity is derived from ``APP_BASE_URL`` because that is the one
        value already distinct per environment. Without it there is nothing
        honest to default to (a constant would make every environment identical
        again, which is the bug), so the deployment is left as it was and told
        what to set. Explicit ``JWT_ISSUER``/``JWT_AUDIENCE`` always win.
        """
        base_url = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
        if base_url:
            if self.jwt_issuer is None:
                self.jwt_issuer = base_url
            if self.jwt_audience is None:
                self.jwt_audience = base_url

        if self.auth_required and self.jwt_issuer and self.jwt_audience:
            # Both claims are now present on everything we mint, so requiring
            # them costs nothing and closes the cross-deployment replay.
            self.jwt_strict_validation = True
        elif self.auth_required and not (self.jwt_issuer and self.jwt_audience):
            logger.warning(
                "SECURITY: JWT issuer/audience are unset while AUTH_REQUIRED=true, "
                "so tokens are not bound to this deployment — a token minted by "
                "another environment sharing SECRET_KEY would be accepted. Set "
                "APP_BASE_URL (or JWT_ISSUER + JWT_AUDIENCE explicitly)."
            )
        return self

    @model_validator(mode="after")
    def _warn_insecure_defaults(self) -> "SecurityConfig":
        """Refuse or warn about dangerous configuration (see _security_posture)."""
        check_insecure_defaults(self)
        return self


# Global instance
_security_config: SecurityConfig | None = None


def get_security_config() -> SecurityConfig:
    """Get or create the global security configuration instance."""
    global _security_config
    if _security_config is None:
        _security_config = SecurityConfig()
        logger.info(
            f"Initialized SecurityConfig (auth_required={_security_config.auth_required})"
        )
    return _security_config
