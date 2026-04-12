"""
Runtime environment helpers.

Provides a single place to resolve the effective application environment so
different modules do not diverge between APP_ENV and ENVIRONMENT.
"""

from __future__ import annotations

import os


def get_runtime_environment(default: str = "development") -> str:
    """Return the effective runtime environment name."""
    value = os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or default
    return value.strip().lower()


def is_production_env() -> bool:
    """Return True when the application is running in production mode."""
    return get_runtime_environment() == "production"
