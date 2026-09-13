"""The message path has to reach the tenant cost ledger too.

``account_turn`` books the *per-request* ledgers — the cost-control middleware,
the Gen AI metrics, the ambient ``LoopBudget`` — and hands back the billed
``(input, output)`` split so the caller can book the one ledger it does not
own: the tenant's cumulative spend (``record_tenant_llm_cost``), which is what
``enforce_tenant_cost_budget`` gates the *next* call on.

The text, streaming and structured paths take that split and book it. The
message path — the flagship agent loop — did not, on either branch, so every
call an agent made was invisible to the tenant's cumulative spend and the cost
cap silently never fired. The sibling of ``test_structured_tenant_cost``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.services.llm.errors import LLMRefusalError
from core.services.llm.message_runtime import generate_messages
from core.services.llm.messages import Message
from core.services.llm.service import LLMService
from core.services.llm.tool_calling import LLMResult

pytestmark = [pytest.mark.unit]

MODEL = "gpt-4o-mini"
HISTORY = [Message.user("q")]


def _service(*, messages: bool) -> LLMService:
    with patch("core.services.llm.service.get_llm_config") as config:
        config.return_value = Mock(
            provider="openai",
            model=MODEL,
            enable_cache=False,
            fallback_chain="",
            max_concurrent_requests=0,
            enable_native_tools=True,
        )
        service = LLMService()
    service.provider = SimpleNamespace(
        supports_native_tools=True, supports_messages=messages
    )
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


class TestNativeMessagePath:
    async def test_the_turn_reaches_the_tenant_ledger(self, booked):
        service = _service(messages=True)
        with patch(
            "core.services.llm.message_runtime._messages_with_retry",
            AsyncMock(return_value=LLMResult(text="ok", tokens_used=300)),
        ):
            await generate_messages(service, HISTORY, model=MODEL)

        assert len(booked) == 1
        assert booked[0] > 0

    async def test_a_refusal_is_booked_before_it_propagates(self, booked):
        """A refusal is generated, billed output — the same rule every other
        path applies. Losing it here loses spend that already happened."""
        service = _service(messages=True)
        with patch(
            "core.services.llm.message_runtime._messages_with_retry",
            AsyncMock(side_effect=LLMRefusalError(category="safety", explanation="no")),
        ):
            with pytest.raises(LLMRefusalError):
                await generate_messages(service, HISTORY, model=MODEL)

        assert len(booked) == 1


class TestDegradedPath:
    async def test_the_transcript_branch_books_exactly_once(self, booked):
        """It returns through ``generate_structured``, which books it already —
        booking again here would double-charge the tenant."""
        service = _service(messages=False)
        service.provider.generate_structured = AsyncMock(  # type: ignore[attr-defined]
            return_value=LLMResult(text="ok", tokens_used=300)
        )

        await generate_messages(service, HISTORY, model=MODEL)

        assert len(booked) == 1
        assert booked[0] > 0
