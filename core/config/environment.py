"""
Runtime environment helpers.

Thin re-export of :mod:`core.utils.runtime_env`, which owns the resolution
logic and stays stdlib-only so the plugin integrity gate and the A2A security
module can share it without importing pydantic through this package's init.
"""

from __future__ import annotations

from core.utils.runtime_env import (
    PRODUCTION,
    get_runtime_environment,
    is_known_environment,
    is_production_env,
)

__all__ = [
    "PRODUCTION",
    "get_runtime_environment",
    "is_known_environment",
    "is_production_env",
]
