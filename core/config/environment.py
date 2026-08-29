"""
Runtime environment helpers.

Thin re-export of :mod:`core.utils.runtime_env`, which owns the resolution
logic and stays stdlib-only so the plugin integrity gate and the A2A security
module can share it without importing pydantic through this package's init.
"""

from __future__ import annotations

from core.utils.runtime_env import (
    PRODUCTION,
    assume_production_when_undeclared,
    get_runtime_environment,
    is_known_environment,
    is_production_env,
    reset_assumed_production,
)

__all__ = [
    "PRODUCTION",
    "assume_production_when_undeclared",
    "get_runtime_environment",
    "is_known_environment",
    "is_production_env",
    "reset_assumed_production",
]
