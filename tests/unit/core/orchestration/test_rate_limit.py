"""Tests for the per-tool sliding-window rate limiter."""

from __future__ import annotations

import pytest
from core.orchestration.rate_limit import (
    ToolRateLimiter,
    ToolRateLimitExceededError,
    get_default_tool_rate_limiter,
    reset_default_tool_rate_limiter,
)

pytestmark = [pytest.mark.unit]


class TestToolRateLimiter:
    def test_allows_up_to_max_calls(self):
        limiter = ToolRateLimiter(max_calls=3, window_seconds=60)
        for _ in range(3):
            limiter.check("send_email", "external_side_effect", tenant_id="t1")

    def test_raises_over_the_cap(self):
        limiter = ToolRateLimiter(max_calls=2, window_seconds=60)
        limiter.check("send_email", "external_side_effect", tenant_id="t1")
        limiter.check("send_email", "external_side_effect", tenant_id="t1")
        with pytest.raises(ToolRateLimitExceededError):
            limiter.check("send_email", "external_side_effect", tenant_id="t1")

    def test_window_expiry_frees_slots(self):
        clock = [0.0]
        limiter = ToolRateLimiter(max_calls=1, window_seconds=10, now=lambda: clock[0])
        limiter.check("send_email", "destructive", tenant_id="t1")
        clock[0] = 11.0
        limiter.check("send_email", "destructive", tenant_id="t1")

    def test_keyed_per_tenant_and_tool(self):
        limiter = ToolRateLimiter(max_calls=1, window_seconds=60)
        limiter.check("send_email", "destructive", tenant_id="t1")
        limiter.check("send_email", "destructive", tenant_id="t2")
        limiter.check("delete_row", "destructive", tenant_id="t1")

    def test_read_only_categories_bypass(self):
        """Only side-effecting categories are limited."""
        limiter = ToolRateLimiter(max_calls=1, window_seconds=60)
        for _ in range(5):
            limiter.check("search", "read_only", tenant_id="t1")


class TestDefaultLimiter:
    def test_disabled_by_default(self, monkeypatch):
        from core.config import orchestration as orch_config

        monkeypatch.delenv("ORCHESTRATOR_TOOL_RATE_LIMIT_ENABLED", raising=False)
        orch_config._orchestration_config = None
        reset_default_tool_rate_limiter()
        try:
            assert get_default_tool_rate_limiter() is None
        finally:
            orch_config._orchestration_config = None
            reset_default_tool_rate_limiter()

    def test_enabled_via_config(self, monkeypatch):
        from core.config import orchestration as orch_config

        monkeypatch.setenv("ORCHESTRATOR_TOOL_RATE_LIMIT_ENABLED", "true")
        monkeypatch.setenv("ORCHESTRATOR_TOOL_RATE_LIMIT_MAX_CALLS", "2")
        orch_config._orchestration_config = None
        reset_default_tool_rate_limiter()
        try:
            limiter = get_default_tool_rate_limiter()
            assert isinstance(limiter, ToolRateLimiter)
        finally:
            orch_config._orchestration_config = None
            reset_default_tool_rate_limiter()
