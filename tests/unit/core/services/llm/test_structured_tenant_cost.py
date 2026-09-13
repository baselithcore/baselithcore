"""Structured generation has to reach the tenant cost ledger too.

``account_turn`` books the *per-request* ledgers — the cost-control middleware,
the Gen AI metrics, the ambient ``LoopBudget`` — and hands back the billed
``(input, output)`` split so the caller can book the one ledger it does not
own: the tenant's cumulative spend (``record_tenant_llm_cost``), which is what
``enforce_tenant_cost_budget`` gates the *next* call on.

The text path and the streaming path take that split and book it. Structured
generation did not, on either branch: a deployment whose agents use tool calling
metered a fraction of its real spend, so the tenant cost cap never tripped and
the per-tenant cost report understated the bill.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.services.llm.errors import LLMRefusalError
from core.services.llm.service import LLMService
from core.services.llm.structured import generate_structured

pytestmark = [pytest.mark.unit]

MODEL = "gpt-4o-mini"


def _service(*, native: bool) -> LLMService:
    with patch("core.services.llm.service.get_llm_config") as config:
        config.return_value = Mock(
            provider="openai",
            model=MODEL,
            enable_cache=False,
            fallback_chain="",
            max_concurrent_requests=0,
            enable_native_tools=native,
        )
        service = LLMService()
    service.provider = SimpleNamespace(supports_native_tools=native)
    service.cost_tracker = None
    return service


@pytest.fixture
def booked(monkeypatch):
    """Capture every ``record_tenant_llm_cost`` call made during generation."""
    recorded: list[float] = []

    async def _record(usd, **_kwargs):
        recorded.append(usd)

    monkeypatch.setattr("core.quotas.cost_enforcement.record_tenant_llm_cost", _record)
    return recorded


class TestCoercionPath:
    """No native tool API: the prompt-coercion fallback through the text API."""

    async def test_the_turn_reaches_the_tenant_ledger(self, booked):
        service = _service(native=False)
        service._generate_with_retry = AsyncMock(return_value=('{"a": 1}', 300))

        await generate_structured(service, "prompt", model=MODEL)

        assert len(booked) == 1
        assert booked[0] > 0

    async def test_a_refusal_is_booked_before_it_propagates(self, booked):
        """A refusal is generated, billed output — the same rule the text path
        applies. Losing it here loses spend that already happened."""
        service = _service(native=False)
        service._generate_with_retry = AsyncMock(
            side_effect=LLMRefusalError(category="safety", explanation="no")
        )

        with pytest.raises(LLMRefusalError):
            await generate_structured(service, "prompt", model=MODEL)

        assert len(booked) == 1


class TestNativePath:
    """The provider's tool-calling API — the same gap, same fix."""

    async def test_the_turn_reaches_the_tenant_ledger(self, booked):
        from core.services.llm.tool_calling import LLMResult

        service = _service(native=True)
        with patch(
            "core.services.llm.structured._native_with_retry",
            AsyncMock(return_value=LLMResult(text="ok", tokens_used=300)),
        ):
            await generate_structured(service, "prompt", model=MODEL)

        assert len(booked) == 1
        assert booked[0] > 0
