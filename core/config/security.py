"""
Security configuration.

Authentication, Security Headers, and Rate Limiting.
"""

import logging
from typing import Annotated, Dict, Set, Optional, List

from pydantic import Field, model_validator, AliasChoices, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    secret_key: Optional[SecretStr] = Field(default=None, alias="SECRET_KEY")
    auth_required: bool = Field(default=True, alias="AUTH_REQUIRED")
    jwt_issuer: Optional[str] = Field(default=None, alias="JWT_ISSUER")
    jwt_audience: Optional[str] = Field(default=None, alias="JWT_AUDIENCE")
    jwt_strict_validation: bool = Field(
        default=False,
        alias="JWT_STRICT_VALIDATION",
        description="When true, reject JWTs missing aud/iss claims (recommended for multi-region deployments).",
    )
    api_key_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("API_KEY_ENABLED", "SECURITY_API_KEY_ENABLED"),
    )

    # CORS — defaults to empty (block all cross-origin) for safety
    allow_origins: List[str] = Field(default_factory=list, alias="ALLOW_ORIGINS")
    trusted_hosts: List[str] = Field(default_factory=list, alias="TRUSTED_HOSTS")

    # API Keys (wrapped in SecretStr to prevent accidental leakage via repr/logs/Sentry)
    api_keys_user: Set[SecretStr] = Field(default_factory=set, alias="API_KEYS_USER")
    api_keys_admin: Set[SecretStr] = Field(default_factory=set, alias="API_KEYS_ADMIN")
    api_keys_job: Set[SecretStr] = Field(default_factory=set, alias="API_KEYS_JOB")

    # Admin Credentials (Legacy/Simple Auth)
    admin_user: str = Field(default="admin", alias="ADMIN_USER")
    admin_pass: Optional[SecretStr] = Field(default=None, alias="ADMIN_PASS")
    admin_pass_hashed: Optional[SecretStr] = Field(
        default=None, alias="ADMIN_PASS_HASHED"
    )

    # === Secrets backend (resolution of credentials) ===
    # 'env' (default, current behaviour) or 'file' (Docker/K8s mounted secrets),
    # plus any backend registered via core.security.secrets.register_secrets_provider.
    secrets_backend: str = Field(default="env", alias="SECRETS_BACKEND")
    secrets_dir: Optional[str] = Field(default=None, alias="SECRETS_DIR")

    # === Encryption at rest ===
    # Mapping of key_id -> secret material (raw base64 32-byte key or passphrase),
    # supplied as "id1:secret1,id2:secret2"; a value without ':' is loaded under
    # the id 'default'. Empty (the default) disables application-level encryption.
    # NoDecode: skip pydantic-settings' JSON decoding so the raw "id:secret,..."
    # string reaches the field validator below (env source would otherwise try
    # json.loads on it and fail).
    data_encryption_keys: Annotated[Dict[str, SecretStr], NoDecode] = Field(
        default_factory=dict, alias="DATA_ENCRYPTION_KEYS"
    )
    data_encryption_active_key_id: Optional[str] = Field(
        default=None, alias="DATA_ENCRYPTION_ACTIVE_KEY_ID"
    )

    # === Rate Limiting ===
    rate_limit_user_per_minute: Optional[int] = Field(
        default=60, alias="RATE_LIMIT_USER_PER_MINUTE"
    )
    rate_limit_admin_per_minute: Optional[int] = Field(
        default=None, alias="RATE_LIMIT_ADMIN_PER_MINUTE"
    )
    rate_limit_job_per_minute: Optional[int] = Field(
        default=None, alias="RATE_LIMIT_JOB_PER_MINUTE"
    )
    rate_limit_window_seconds: int = Field(
        default=60, alias="RATE_LIMIT_WINDOW_SECONDS", ge=1
    )

    # === Security Headers ===
    security_headers_enabled: bool = Field(
        default=True, alias="SECURITY_HEADERS_ENABLED"
    )
    content_security_policy: Optional[str] = Field(
        default=None, alias="CONTENT_SECURITY_POLICY"
    )
    enable_hsts: bool = Field(default=True, alias="ENABLE_HSTS")
    hsts_max_age: int = Field(default=31536000, alias="HSTS_MAX_AGE")
    frame_options: str = Field(default="DENY", alias="X_FRAME_OPTIONS")
    permissions_policy: Optional[str] = Field(default=None, alias="PERMISSIONS_POLICY")

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
    def _coerce_to_secret_set(cls, v):
        """Coerce comma-separated strings or iterables of mixed types to ``Set[SecretStr]``."""
        if v is None or v == "":
            return set()
        if isinstance(v, str):
            items = [s.strip() for s in v.split(",") if s.strip()]
            return {SecretStr(s) for s in items}
        if isinstance(v, (list, set, tuple)):
            return {x if isinstance(x, SecretStr) else SecretStr(str(x)) for x in v}
        return v

    @field_validator("data_encryption_keys", mode="before")
    @classmethod
    def _parse_encryption_keys(cls, v):
        """Parse ``id:secret`` pairs (comma-separated) into ``Dict[str, SecretStr]``.

        A bare value without ``:`` is loaded under the id ``default`` so the
        common single-key case stays simple. Already-parsed dicts pass through.
        """
        if v is None or v == "":
            return {}
        if isinstance(v, dict):
            return {
                str(k): (val if isinstance(val, SecretStr) else SecretStr(str(val)))
                for k, val in v.items()
            }
        if isinstance(v, str):
            parsed: Dict[str, SecretStr] = {}
            for entry in (e.strip() for e in v.split(",")):
                if not entry:
                    continue
                if ":" in entry:
                    key_id, secret = entry.split(":", 1)
                    parsed[key_id.strip()] = SecretStr(secret)
                else:
                    parsed["default"] = SecretStr(entry)
            return parsed
        return v

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
    def _warn_insecure_defaults(self) -> "SecurityConfig":
        """Emit loud warnings for dangerous default configurations."""
        if self.auth_required and not self.secret_key:
            raise ValueError(
                "SECRET_KEY is required when AUTH_REQUIRED=true. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        if self.secret_key and len(self.secret_key.get_secret_value()) < 32:
            raise ValueError(
                "SECRET_KEY is too short. Minimum length is 32 characters."
            )
        if self.admin_pass and self.admin_pass.get_secret_value() in (
            "password",
            "changeme",
            "admin",
        ):
            raise ValueError(
                "SECURITY: ADMIN_PASS is set to an insecure default ('password', 'changeme', or 'admin'). "
                "Change it before deploying to production."
            )
        if "*" in self.allow_origins:
            if self.admin_pass:
                # Wildcard + Admin Pass is a critical vulnerability for the admin router/console.
                raise ValueError(
                    "SECURITY CRITICAL: 'ALLOW_ORIGINS' contains '*' (wildcard) while 'ADMIN_PASS' is set. "
                    "Cross-origin credentialed requests (CORS) are disabled for wildcards, which will "
                    "break the Admin Console. You MUST explicitly list allowed origins or use a specific domain "
                    "for production."
                )
            logger.warning(
                "SECURITY: 'ALLOW_ORIGINS' contains '*' (wildcard). "
                "Cross-origin requests will be allowed from ANY site, but credentials (cookies/auth) "
                "will be disabled by the framework for security."
            )
        return self


# Global instance
_security_config: Optional[SecurityConfig] = None


def get_security_config() -> SecurityConfig:
    """Get or create the global security configuration instance."""
    global _security_config
    if _security_config is None:
        _security_config = SecurityConfig()
        logger.info(
            f"Initialized SecurityConfig (auth_required={_security_config.auth_required})"
        )
    return _security_config
