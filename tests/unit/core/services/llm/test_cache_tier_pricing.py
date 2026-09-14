"""Cached prompt tokens must be priced as cached, on every path.

The providers report three prompt-side buckets — fresh input, cache reads
(~0.1x input) and cache writes (~1.25x input) — and ``Usage`` has carried all
three for a while. The resolver every accounting site used
(``usage_split``, now :func:`~core.services.llm.usage.billed_usage`) folded
them into one "input" number, and each site then forwarded that as
``prompt_tokens`` while taking the ``cache_read_tokens=0`` default. So a cache
read was billed at the *full* input rate in the ``LoopBudget``, in the tenant
ledger and on the span — and the better the prompt cache worked, the worse the
overcharge.

The numbers below are the reviewer's measurement: 200 fresh + 20 000 cached
input tokens and 500 output tokens is $0.0454 as shipped versus $0.0094
cache-aware on ``claude-sonnet-5`` (4.8x), $0.00333 versus $0.00063 on
``gpt-4o-mini`` (5.3x).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.services.llm.message_runtime import generate_messages
from core.services.llm.messages import Message
from core.services.llm.service import LLMService
from core.services.llm.stream_events import StreamEnd, generate_stream_events
from core.services.llm.structured import generate_structured
from core.services.llm.tool_calling import LLMResult
from core.services.llm.usage import Usage, billed_usage

pytestmark = [pytest.mark.unit]

MODEL = "claude-sonnet-5"
HISTORY = [Message.user("q")]

#: The reviewer's call: a well-cached turn.
CACHED_TURN = Usage(input_tokens=200, output_tokens=500, cache_read_tokens=20_000)
TOTAL_TOKENS = CACHED_TURN.total

#: What that call costs when the cache tier is honoured...
CACHE_AWARE_USD = 0.0094
#: ...and what it cost when every cached token was billed as fresh input.
CONFLATED_USD = 0.0454


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


@pytest.fixture
def booked(monkeypatch):
    """Capture every USD figure booked on the tenant's ledger."""
    recorded: list[float] = []

    async def _record(usd, **_kwargs):
        recorded.append(usd)

    monkeypatch.setattr("core.quotas.cost_enforcement.record_tenant_llm_cost", _record)
    return recorded


class TestThePriceOfTheReviewersCall:
    """The arithmetic the rest of the module asserts through the paths."""

    def test_the_cache_aware_and_conflated_figures(self):
        from core.models.pricing import estimate_cost

        cache_aware = estimate_cost(
            MODEL,
            CACHED_TURN.input_tokens,
            CACHED_TURN.output_tokens,
            cache_read_tokens=CACHED_TURN.cache_read_tokens,
        )
        conflated = estimate_cost(
            MODEL, CACHED_TURN.prompt_tokens, CACHED_TURN.output_tokens
        )

        assert cache_aware == pytest.approx(CACHE_AWARE_USD)
        assert conflated == pytest.approx(CONFLATED_USD)
        assert conflated / cache_aware == pytest.approx(4.83, abs=0.01)

    def test_the_same_ratio_on_a_second_family(self):
        from core.models.pricing import estimate_cost

        cache_aware = estimate_cost(
            "gpt-4o-mini",
            CACHED_TURN.input_tokens,
            CACHED_TURN.output_tokens,
            cache_read_tokens=CACHED_TURN.cache_read_tokens,
        )
        conflated = estimate_cost(
            "gpt-4o-mini", CACHED_TURN.prompt_tokens, CACHED_TURN.output_tokens
        )

        assert conflated / cache_aware == pytest.approx(5.29, abs=0.01)


class TestTheResolverKeepsTheBucketsApart:
    def test_a_metered_record_is_returned_whole(self):
        billed = billed_usage(CACHED_TURN, fallback_input=1, fallback_total=2)

        assert billed is CACHED_TURN
        assert billed.cache_read_tokens == 20_000
        # The trap: the old resolver answered this number as "input".
        assert billed.prompt_tokens == 20_200

    def test_an_unmetered_call_still_falls_back_to_the_estimate(self):
        billed = billed_usage(None, fallback_input=100, fallback_total=140)

        assert (billed.input_tokens, billed.output_tokens) == (100, 40)
        assert billed.estimated is True
        assert billed.cache_read_tokens == 0


class TestEveryPathBooksTheCacheAwarePrice:
    async def test_the_text_path(self, booked):
        service = _service()

        async def _generate(**kwargs):
            kwargs["usage_sink"].append(CACHED_TURN)
            return "hi", TOTAL_TOKENS

        service._generate_with_retry = _generate  # type: ignore[method-assign]

        await service.generate_response("q")

        assert booked == [pytest.approx(CACHE_AWARE_USD)]

    async def test_the_streaming_path(self, booked):
        service = _service()

        async def _open_stream(_service, _prompt, _model, stream_kwargs):
            stream_kwargs["usage_sink"].append(CACHED_TURN)

            async def _chunks():
                yield "hi", TOTAL_TOKENS

            return _chunks(), None, "openai", MODEL

        with patch("core.services.llm._streaming.open_stream", _open_stream):
            _ = [c async for c in service.generate_response_stream("q")]

        assert booked == [pytest.approx(CACHE_AWARE_USD)]

    async def test_the_native_structured_path(self, booked):
        service = _service(native=True)
        result = LLMResult(text="hi", tokens_used=TOTAL_TOKENS, usage=CACHED_TURN)

        with patch(
            "core.services.llm.structured._native_with_retry",
            AsyncMock(return_value=result),
        ):
            await generate_structured(service, "q", model=MODEL)

        assert booked == [pytest.approx(CACHE_AWARE_USD)]

    async def test_the_coercion_structured_path(self, booked):
        service = _service(native=False)

        async def _generate(**kwargs):
            kwargs["usage_sink"].append(CACHED_TURN)
            return '{"a": 1}', TOTAL_TOKENS

        service._generate_with_retry = _generate  # type: ignore[method-assign]

        await generate_structured(service, "q", model=MODEL)

        assert booked == [pytest.approx(CACHE_AWARE_USD)]

    async def test_the_message_path(self, booked):
        service = _service(messages=True)
        result = LLMResult(text="hi", tokens_used=TOTAL_TOKENS, usage=CACHED_TURN)

        with patch(
            "core.services.llm.message_runtime._messages_with_retry",
            AsyncMock(return_value=result),
        ):
            await generate_messages(service, HISTORY, model=MODEL)

        assert booked == [pytest.approx(CACHE_AWARE_USD)]

    async def test_the_streamed_event_path(self, booked):
        service = _service(native=True)
        result = LLMResult(text="hi", tokens_used=TOTAL_TOKENS, usage=CACHED_TURN)

        async def _stream(_prompt, _model, **_kwargs):
            yield StreamEnd(result)

        service.provider.generate_structured_stream = _stream  # type: ignore[attr-defined]

        _ = [e async for e in generate_stream_events(service, "q", model=MODEL)]

        assert booked == [pytest.approx(CACHE_AWARE_USD)]


class TestTheRequestBudgetSeesTheSamePrice:
    async def test_a_well_cached_run_no_longer_aborts_five_times_too_early(
        self, booked
    ):
        """``budget_usd`` between the two figures: the run must complete."""
        from core.orchestration.budget_context import activate_budget, deactivate_budget
        from core.orchestration.limits import LoopBudget, LoopLimits

        service = _service(native=True)
        result = LLMResult(text="hi", tokens_used=TOTAL_TOKENS, usage=CACHED_TURN)
        budget = LoopBudget(limits=LoopLimits(budget_usd=0.02))
        token = activate_budget(budget)
        try:
            with patch(
                "core.services.llm.structured._native_with_retry",
                AsyncMock(return_value=result),
            ):
                answer = await generate_structured(service, "q", model=MODEL)
        finally:
            deactivate_budget(token)

        assert answer.text == "hi"
        assert budget.cost_usd == pytest.approx(CACHE_AWARE_USD)


class TestTheSpanReportsTheBucketsSeparately:
    async def test_the_semconv_attributes_split_fresh_input_from_cache(self):
        from core.services.llm._accounting import set_usage_span_attributes

        attributes: dict = {}
        span = SimpleNamespace(set_attribute=lambda k, v: attributes.update({k: v}))

        set_usage_span_attributes(span, CACHED_TURN)

        # 200, not 20 200: gen_ai.usage.input_tokens means fresh input, the
        # way every provider reports it.
        assert attributes["gen_ai.usage.input_tokens"] == 200
        assert attributes["gen_ai.usage.output_tokens"] == 500
        assert attributes["gen_ai.usage.cache_read_tokens"] == 20_000
        assert "gen_ai.usage.cache_write_tokens" not in attributes
