"""Environment helpers for the security middleware (500-line-cap split).

Kept as module-level functions (not inlined) so tests can patch them.
"""

from __future__ import annotations

import os


def _is_production_env() -> bool:
    """Whether the runtime environment is production (lazy import, testable)."""
    from core.config.environment import is_production_env

    return is_production_env()


def _lockout_fail_open() -> bool:
    """Operator opt-out: prefer availability over the lockout control."""
    return os.getenv("BASELITH_LOCKOUT_FAIL_OPEN", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _redis_backend_declared() -> bool:
    """Whether the deployment declared Redis as its cache backend.

    Separates "the shared store is down" from "there is no shared store". A
    deployment that never configured Redis runs the per-process fallback by
    design, so refusing its privileged auth would be a self-inflicted outage
    rather than a security control. Same rule the rate limiter applies when
    resolving its fail mode.
    """
    try:
        from core.config import get_storage_config

        return getattr(get_storage_config(), "cache_backend", "") == "redis"
    except Exception:  # pragma: no cover - config unavailable in minimal envs
        return False


__all__ = ["_is_production_env", "_lockout_fail_open", "_redis_backend_declared"]
