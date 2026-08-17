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


__all__ = ["_is_production_env", "_lockout_fail_open"]
