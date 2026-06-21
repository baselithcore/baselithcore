"""
Core Middleware Module

Provides HTTP middleware components for the baselith-core.
"""

from .cost_control import (
    CostController,
    CostControlMiddleware,
    CostStats,
    BudgetExceededError,
    cost_controller,
)
from .security import (
    SecurityHeadersMiddleware,
    RequestSizeLimitMiddleware,
    RateLimiter,
    rate_limiter,
    require_user,
    require_admin,
    require_admin_or_job,
    verify_admin_password,
    check_admin_lockout,
    record_admin_failure,
    clear_admin_failures,
)
from .quota import QuotaMiddleware
from .tenant import TenantMiddleware

__all__ = [
    # Cost Control
    "CostController",
    "CostControlMiddleware",
    "CostStats",
    "BudgetExceededError",
    "cost_controller",
    # Security
    "SecurityHeadersMiddleware",
    "RequestSizeLimitMiddleware",
    "RateLimiter",
    "rate_limiter",
    "require_user",
    "require_admin",
    "require_admin_or_job",
    "verify_admin_password",
    "check_admin_lockout",
    "record_admin_failure",
    "clear_admin_failures",
    # Tenant
    "TenantMiddleware",
    # Quotas
    "QuotaMiddleware",
]
