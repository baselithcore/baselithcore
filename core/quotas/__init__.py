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
    "QuotaManager",
    "get_quota_manager",
    "QuotaExceededError",
    "QuotaStatus",
    "WindowStatus",
    "QuotaWindow",
    "QuotaStore",
    "InMemoryQuotaStore",
    "RedisQuotaStore",
    "build_default_store",
]
