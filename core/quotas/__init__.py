"""
Per-key usage quotas — persistent request budgets per identity over calendar
windows (daily/monthly), distinct from per-minute rate limiting and per-request
cost control. Opt-in via ``QUOTAS_ENABLED``.
"""

from core.quotas.cost_enforcement import (
    enforce_tenant_cost_budget,
    llm_call_cost_usd,
    record_tenant_llm_cost,
)
from core.quotas.manager import (
    CostBudgetExceededError,
    CostWindowStatus,
    QuotaExceededError,
    QuotaManager,
    QuotaStatus,
    QuotaWindow,
    TenantCostStatus,
    WindowStatus,
    get_quota_manager,
)
from core.quotas.store import (
    InMemoryQuotaStore,
    QuotaStore,
    RedisQuotaStore,
    build_default_store,
)

__all__ = [
    "CostBudgetExceededError",
    "CostWindowStatus",
    "InMemoryQuotaStore",
    "QuotaExceededError",
    "QuotaManager",
    "QuotaStatus",
    "QuotaStore",
    "QuotaWindow",
    "RedisQuotaStore",
    "TenantCostStatus",
    "WindowStatus",
    "build_default_store",
    "enforce_tenant_cost_budget",
    "get_quota_manager",
    "llm_call_cost_usd",
    "record_tenant_llm_cost",
]
