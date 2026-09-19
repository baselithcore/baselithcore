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
from .http_metrics import HTTPMetricsMiddleware
from .idempotency import IdempotencyMiddleware
from .plugin_activation import PluginActivationMiddleware
from .plugin_context import PluginContextMiddleware
from .quota import QuotaMiddleware
from .security import (
    RateLimiter,
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    check_admin_lockout,
    clear_admin_failures,
    clear_request_size_overrides,
    rate_limiter,
    record_admin_failure,
    register_request_size_override,
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
    "register_request_size_override",
    "clear_request_size_overrides",
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
    # Idempotency
    "IdempotencyMiddleware",
    # Plugin activation
    "PluginActivationMiddleware",
    # Plugin context (request → owning plugin attribution)
    "PluginContextMiddleware",
    # Tenant
    "TenantMiddleware",
    # Quotas
    "QuotaMiddleware",
    # HTTP RED metrics
    "HTTPMetricsMiddleware",
]
