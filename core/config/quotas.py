"""
Per-key usage quota configuration.

Quotas are persistent request budgets per identity (API key / user) over a
calendar window — distinct from per-minute rate limiting and from per-request
cost control. Opt-in and default-off.
"""

import logging
from typing import Dict, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class QuotaConfig(BaseSettings):
    """Configuration for per-key usage quotas."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=False, alias="QUOTAS_ENABLED")
    # Default budgets applied to every identity. ``None`` (or 0) = unlimited.
    daily_request_limit: Optional[int] = Field(
        default=None, alias="QUOTA_DAILY_REQUESTS", ge=0
    )
    monthly_request_limit: Optional[int] = Field(
        default=None, alias="QUOTA_MONTHLY_REQUESTS", ge=0
    )
    # Default budgets applied to every TENANT (aggregate across all its members),
    # distinct from the per-identity limits above. ``None``/0 = unlimited.
    tenant_daily_request_limit: Optional[int] = Field(
        default=None, alias="QUOTA_TENANT_DAILY_REQUESTS", ge=0
    )
    tenant_monthly_request_limit: Optional[int] = Field(
        default=None, alias="QUOTA_TENANT_MONTHLY_REQUESTS", ge=0
    )
    # Backend: 'memory' (single-process) or 'redis' (shared across workers).
    backend: str = Field(default="redis", alias="QUOTA_BACKEND")


_quota_config: Optional[QuotaConfig] = None
# Programmatic per-identity overrides: identity -> (daily, monthly). Each value
# may be None to fall back to the config default for that window.
_per_key_overrides: Dict[str, tuple[Optional[int], Optional[int]]] = {}
# Per-tenant overrides: tenant_id -> (daily, monthly) — the tenant's plan/quota.
_per_tenant_overrides: Dict[str, tuple[Optional[int], Optional[int]]] = {}


def get_quota_config() -> QuotaConfig:
    """Get or create the global quota configuration instance."""
    global _quota_config
    if _quota_config is None:
        _quota_config = QuotaConfig()
    return _quota_config


def set_key_quota(
    identity: str, *, daily: Optional[int] = None, monthly: Optional[int] = None
) -> None:
    """Override the per-window limits for a specific identity (runtime)."""
    _per_key_overrides[identity] = (daily, monthly)


def get_key_overrides(identity: str) -> tuple[Optional[int], Optional[int]]:
    """Return the (daily, monthly) overrides for an identity, or ``(None, None)``."""
    return _per_key_overrides.get(identity, (None, None))


def set_tenant_quota(
    tenant_id: str, *, daily: Optional[int] = None, monthly: Optional[int] = None
) -> None:
    """Override the per-window limits for a specific tenant (runtime)."""
    _per_tenant_overrides[tenant_id] = (daily, monthly)


def get_tenant_overrides(tenant_id: str) -> tuple[Optional[int], Optional[int]]:
    """Return the (daily, monthly) overrides for a tenant, or ``(None, None)``."""
    return _per_tenant_overrides.get(tenant_id, (None, None))
