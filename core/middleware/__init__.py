"""
Core Middleware Module

Provides HTTP middleware components for the baselith-core.
"""

from .cost_control import (
    BudgetExceededError,
    CostController,
    CostControlMiddleware,
    CostStats,
    cost_controller,
)
from .csrf import CSRFOriginMiddleware
from .plugin_activation import PluginActivationMiddleware
from .quota import QuotaMiddleware
from .security import (
    RateLimiter,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    check_admin_lockout,
    clear_admin_failures,
    rate_limiter,
    record_admin_failure,
    require_admin,
    require_admin_or_job,
    require_user,
    verify_admin_password,
    verify_admin_password_async,
)
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
    "verify_admin_password_async",
    "check_admin_lockout",
    "record_admin_failure",
    "clear_admin_failures",
    # CSRF
    "CSRFOriginMiddleware",
    # Plugin activation
    "PluginActivationMiddleware",
    # Tenant
    "TenantMiddleware",
    # Quotas
    "QuotaMiddleware",
]
