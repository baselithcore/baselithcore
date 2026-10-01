"""Errors raised by the shared inference services."""

from __future__ import annotations

from core.exceptions import BaselithError


class InferenceError(BaselithError):
    """A remote inference call failed for good (retries exhausted or 4xx)."""


class InferenceConfigError(InferenceError):
    """The service is misconfigured (missing URL, forbidden mode, bad scope)."""


class TenantScopeError(InferenceConfigError):
    """A vector operation was attempted without a valid tenant/plugin scope."""
