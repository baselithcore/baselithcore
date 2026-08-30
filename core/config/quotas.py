"""
Per-key usage quota configuration.

Quotas are persistent request budgets per identity (API key / user) over a
calendar window — distinct from per-minute rate limiting and from per-request
cost control. Opt-in and default-off.
"""

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class QuotaConfig(BaseSettings):
    """Configuration for per-key usage quotas."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    enabled: bool = Field(default=False, alias="QUOTAS_ENABLED")
    # Default budgets applied to every identity. ``None`` (or 0) = unlimited.
    daily_request_limit: int | None = Field(
        default=None, alias="QUOTA_DAILY_REQUESTS", ge=0
    )
    monthly_request_limit: int | None = Field(
        default=None, alias="QUOTA_MONTHLY_REQUESTS", ge=0
    )
    # Default budgets applied to every TENANT (aggregate across all its members),
    # distinct from the per-identity limits above. ``None``/0 = unlimited.
    tenant_daily_request_limit: int | None = Field(
        default=None, alias="QUOTA_TENANT_DAILY_REQUESTS", ge=0
    )
    tenant_monthly_request_limit: int | None = Field(
        default=None, alias="QUOTA_TENANT_MONTHLY_REQUESTS", ge=0
    )
    # Cumulative dollar-cost budgets per TENANT over the same calendar windows.
    # Distinct from the per-run ``LoopLimits.budget_usd`` cap: this is the
    # tenant's aggregate LLM spend across all requests. ``None``/0 = unlimited.
    tenant_daily_cost_limit_usd: float | None = Field(
        default=None, alias="QUOTA_TENANT_DAILY_COST_USD", ge=0
    )
    tenant_monthly_cost_limit_usd: float | None = Field(
        default=None, alias="QUOTA_TENANT_MONTHLY_COST_USD", ge=0
    )
    # Backend: 'memory' (single-process) or 'redis' (shared across workers).
    backend: str = Field(default="redis", alias="QUOTA_BACKEND")


_quota_config: QuotaConfig | None = None
# Programmatic per-identity overrides: identity -> (daily, monthly). Each value
# may be None to fall back to the config default for that window.
_per_key_overrides: dict[str, tuple[int | None, int | None]] = {}
# Per-tenant overrides: tenant_id -> (daily, monthly) — the tenant's plan/quota.
_per_tenant_overrides: dict[str, tuple[int | None, int | None]] = {}
# Per-tenant COST overrides: tenant_id -> (daily USD, monthly USD).
_per_tenant_cost_overrides: dict[str, tuple[float | None, float | None]] = {}


def get_quota_config() -> QuotaConfig:
    """Get or create the global quota configuration instance."""
    global _quota_config
    if _quota_config is None:
        _quota_config = QuotaConfig()
    return _quota_config


def set_key_quota(
    identity: str, *, daily: int | None = None, monthly: int | None = None
) -> None:
    """Override the per-window limits for a specific identity (runtime)."""
    _per_key_overrides[identity] = (daily, monthly)


def get_key_overrides(identity: str) -> tuple[int | None, int | None]:
    """Return the (daily, monthly) overrides for an identity, or ``(None, None)``."""
    return _per_key_overrides.get(identity, (None, None))


def set_tenant_quota(
    tenant_id: str, *, daily: int | None = None, monthly: int | None = None
) -> None:
    """Override the per-window limits for a specific tenant (runtime)."""
    _per_tenant_overrides[tenant_id] = (daily, monthly)


def get_tenant_overrides(tenant_id: str) -> tuple[int | None, int | None]:
    """Return the (daily, monthly) overrides for a tenant, or ``(None, None)``."""
    return _per_tenant_overrides.get(tenant_id, (None, None))


def set_tenant_cost_budget(
    tenant_id: str, *, daily: float | None = None, monthly: float | None = None
) -> None:
    """Override the per-window USD cost budgets for a specific tenant."""
    _per_tenant_cost_overrides[tenant_id] = (daily, monthly)


def get_tenant_cost_overrides(tenant_id: str) -> tuple[float | None, float | None]:
    """Return the (daily USD, monthly USD) cost overrides, or ``(None, None)``."""
    return _per_tenant_cost_overrides.get(tenant_id, (None, None))
