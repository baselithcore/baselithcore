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
    RateLimiter,
    rate_limiter,
    require_user,
    require_admin,
    require_admin_or_job,
    verify_admin_password,
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
    "RateLimiter",
    "rate_limiter",
    "require_user",
    "require_admin",
    "require_admin_or_job",
    "verify_admin_password",
    # Tenant
    "TenantMiddleware",
]
