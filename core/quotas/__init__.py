"""
Per-key usage quotas — persistent request budgets per identity over calendar
windows (daily/monthly), distinct from per-minute rate limiting and per-request
cost control. Opt-in via ``QUOTAS_ENABLED``.
"""

from core.quotas.manager import (
    QuotaExceededError,
    QuotaManager,
    QuotaStatus,
    QuotaWindow,
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
    "InMemoryQuotaStore",
    "QuotaExceededError",
    "QuotaManager",
    "QuotaStatus",
    "QuotaStore",
    "QuotaWindow",
    "RedisQuotaStore",
    "WindowStatus",
    "build_default_store",
    "get_quota_manager",
]
