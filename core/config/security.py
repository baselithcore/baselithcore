"""
Security configuration.

Authentication, Security Headers, and Rate Limiting.
"""

import logging
from typing import Set, Optional, List

from pydantic import Field, model_validator, AliasChoices, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class SecurityConfig(BaseSettings):
    """
    Security configuration.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === Auth & Secrets ===
    secret_key: Optional[SecretStr] = Field(default=None, alias="SECRET_KEY")
    auth_required: bool = Field(default=True, alias="AUTH_REQUIRED")
    api_key_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("API_KEY_ENABLED", "SECURITY_API_KEY_ENABLED"),
    )

    # CORS — defaults to empty (block all cross-origin) for safety
    allow_origins: List[str] = Field(default_factory=list, alias="ALLOW_ORIGINS")

    # API Keys
    api_keys_user: Set[str] = Field(default_factory=set, alias="API_KEYS_USER")
    api_keys_admin: Set[str] = Field(default_factory=set, alias="API_KEYS_ADMIN")
    api_keys_job: Set[str] = Field(default_factory=set, alias="API_KEYS_JOB")

    # Admin Credentials (Legacy/Simple Auth)
    admin_user: str = Field(default="admin", alias="ADMIN_USER")
    admin_pass: Optional[SecretStr] = Field(default=None, alias="ADMIN_PASS")
    admin_pass_hashed: Optional[SecretStr] = Field(
        default=None, alias="ADMIN_PASS_HASHED"
    )

    # === Rate Limiting ===
    rate_limit_user_per_minute: Optional[int] = Field(
        default=None, alias="RATE_LIMIT_USER_PER_MINUTE"
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
    enable_hsts: bool = Field(default=False, alias="ENABLE_HSTS")
    hsts_max_age: int = Field(default=31536000, alias="HSTS_MAX_AGE")
    frame_options: str = Field(default="DENY", alias="X_FRAME_OPTIONS")
    permissions_policy: Optional[str] = Field(default=None, alias="PERMISSIONS_POLICY")

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
            logger.warning(
                "SECURITY: ADMIN_PASS is set to an insecure default. "
                "Change it before deploying to production."
            )
        if "*" in self.allow_origins:
            logger.warning(
                "SECURITY: ALLOW_ORIGINS contains '*' (wildcard). "
                "This allows any origin to make cross-origin requests."
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
