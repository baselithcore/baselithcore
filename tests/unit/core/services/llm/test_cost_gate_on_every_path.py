"""The tenant cost cap has to gate every path that can spend.

``enforce_tenant_cost_budget`` is the pre-call gate and the only thing that
raises :class:`CostBudgetExceededError`. It was called from the text path and
the streaming path only — while *four* paths write the ledger it reads. A
deployment whose traffic is agentic (native tool calling, or the message API
the agent loop runs on) therefore recorded spend that nothing ever checked:
the cap could be configured, exceeded, and never trip.

Each test here drives one path with a tenant already over its daily cap and
asserts two things: the call is refused, and the provider was never reached —
a gate that fires after the provider has answered is not a gate.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.config.quotas import QuotaConfig
from core.context import set_tenant_context
from core.quotas.manager import CostBudgetExceededError, QuotaManager
from core.quotas.store import InMemoryQuotaStore
from core.services.llm.message_runtime import generate_messages
from core.services.llm.messages import Message
from core.services.llm.service import LLMService
from core.services.llm.stream_events import generate_stream_events
from core.services.llm.structured import generate_structured
from core.services.llm.tool_calling import LLMResult

pytestmark = [pytest.mark.unit]

MODEL = "gpt-4o-mini"
HISTORY = [Message.user("q")]


@pytest.fixture(autouse=True)
def _isolated_token_ledger():
    """Keep this module's token reports out of the process-wide cost context.

    ``cost_controller`` counts tokens in a ContextVar that is initialized —
    and never restored — by ``tests/unit/core/middleware/test_cost_control``,
    so anything reported here would accumulate for the rest of the session
    and trip its 10 000-token agent cap in unrelated tests.
    """
    from core.middleware.cost_control import _cost_context

    token = _cost_context.set(None)
    try:
        yield
    finally:
        _cost_context.reset(token)


@pytest.fixture
async def over_budget(monkeypatch):
    """A tenant whose daily USD cap is already blown."""
    manager = QuotaManager(
        config=QuotaConfig(
            QUOTAS_ENABLED=True,
            QUOTA_BACKEND="memory",
            QUOTA_TENANT_DAILY_COST_USD=1.00,
        ),
        store=InMemoryQuotaStore(),
    )
    set_tenant_context("acme")
    await manager.record_tenant_cost("acme", 1.50)
    monkeypatch.setattr(
        "core.quotas.cost_enforcement.get_quota_manager", lambda: manager
    )
    return manager


def _service(*, native: bool = False, messages: bool = False) -> LLMService:
    with patch("core.services.llm.service.get_llm_config") as config:
        config.return_value = Mock(
            provider="openai",
            model=MODEL,
            enable_cache=False,
            fallback_chain="",
            max_concurrent_requests=0,
            enable_native_tools=native or messages,
            thinking_enabled=False,
        )
        service = LLMService()
    service.provider = SimpleNamespace(
        supports_native_tools=native or messages,
        supports_messages=messages,
    )
    service.cost_tracker = None
    return service


class TestPathsThatAlwaysHadTheGate:
    """The two that were right — kept here so a regression is visible."""

    async def test_the_text_path_is_refused(self, over_budget):
        service = _service()
        service._generate_with_retry = AsyncMock(return_value=("hi", 10))

        with pytest.raises(CostBudgetExceededError):
            await service.generate_response("q")

        assert not service._generate_with_retry.called

    async def test_the_streaming_path_is_refused(self, over_budget):
        service = _service()
        opened = AsyncMock()

        with patch("core.services.llm._streaming.open_stream", opened):
            with pytest.raises(CostBudgetExceededError):
                _ = [c async for c in service.generate_response_stream("q")]

        assert not opened.called


class TestPathsThatWroteTheLedgerWithoutReadingIt:
    """Native tool calling and the agent-loop message API."""

    async def test_the_native_structured_path_is_refused(self, over_budget):
        service = _service(native=True)
        native = AsyncMock(return_value=LLMResult(text="hi", tokens_used=10))

        with patch("core.services.llm.structured._native_with_retry", native):
            with pytest.raises(CostBudgetExceededError):
                await generate_structured(service, "q", model=MODEL)

        assert not native.called

    async def test_the_coercion_structured_path_is_refused(self, over_budget):
        service = _service(native=False)
        service._generate_with_retry = AsyncMock(return_value=('{"a": 1}', 10))

        with pytest.raises(CostBudgetExceededError):
            await generate_structured(service, "q", model=MODEL)

        assert not service._generate_with_retry.called

    async def test_the_message_path_is_refused(self, over_budget):
        service = _service(messages=True)
        turn = AsyncMock(return_value=LLMResult(text="hi", tokens_used=10))

        with patch("core.services.llm.message_runtime._messages_with_retry", turn):
            with pytest.raises(CostBudgetExceededError):
                await generate_messages(service, HISTORY, model=MODEL)

        assert not turn.called

    async def test_the_streamed_event_path_is_refused(self, over_budget):
        """The fifth caller of the provider, and the same omission."""
        service = _service(native=True)
        calls: list[tuple] = []

        async def _stream(*args, **kwargs):
            calls.append((args, kwargs))
            yield None  # pragma: no cover - never reached

        service.provider.generate_structured_stream = _stream  # type: ignore[attr-defined]

        with pytest.raises(CostBudgetExceededError):
            _ = [e async for e in generate_stream_events(service, "q", model=MODEL)]

        assert not calls


class TestTheGateStillPassesWhenUnderBudget:
    """The gate must not become a blanket refusal."""

    async def test_a_tenant_in_credit_reaches_the_provider(self, monkeypatch):
        manager = QuotaManager(
            config=QuotaConfig(
                QUOTAS_ENABLED=True,
                QUOTA_BACKEND="memory",
                QUOTA_TENANT_DAILY_COST_USD=10.00,
            ),
            store=InMemoryQuotaStore(),
        )
        set_tenant_context("acme")
        monkeypatch.setattr(
            "core.quotas.cost_enforcement.get_quota_manager", lambda: manager
        )
        service = _service(messages=True)

        with patch(
            "core.services.llm.message_runtime._messages_with_retry",
            AsyncMock(return_value=LLMResult(text="hi", tokens_used=10)),
        ):
            result = await generate_messages(service, HISTORY, model=MODEL)

        assert result.text == "hi"
