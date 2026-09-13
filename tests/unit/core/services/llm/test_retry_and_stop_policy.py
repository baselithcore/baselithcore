"""Retry eligibility by exception class, and the stop-reason policy.

The retry layers used to decide by substring on ``str(exc)``: a 500, a
dropped connection and a read timeout carry none of those words, so the three
most common transient failures were never retried.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.services.llm.errors import (
    LLMClientError,
    LLMConnectionError,
    LLMRefusalError,
    LLMServerError,
    LLMTimeoutError,
)
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.service import LLMService
from core.services.llm.tool_calling import LLMResult


def _service():
    with patch("core.services.llm.service.get_llm_config") as mock_config:
        mock_config.return_value = Mock(
            provider="ollama",
            model="m",
            enable_cache=False,
            enable_native_tools=True,
            fallback_chain="",
            max_concurrent_requests=0,
        )
        return LLMService()


@pytest.mark.asyncio
class TestTextPathRetryEligibility:
    @pytest.mark.parametrize(
        "transient",
        [
            LLMServerError("upstream 503", status_code=503),
            LLMConnectionError("connection reset"),
            LLMTimeoutError("read timeout"),
        ],
    )
    async def test_transient_failures_are_retried(self, transient):
        service = _service()
        service.provider = Mock()
        service.provider.generate = AsyncMock(side_effect=[transient, ("ok", 10)])

        assert await service.generate_response("q") == "ok"
        assert service.provider.generate.call_count == 2

    async def test_client_errors_fail_fast(self):
        service = _service()
        service.provider = Mock()
        service.provider.generate = AsyncMock(
            side_effect=LLMClientError("unknown model", status_code=404)
        )

        with pytest.raises(Exception):
            await service.generate_response("q")
        assert service.provider.generate.call_count == 1

    async def test_refusals_are_never_retried(self):
        service = _service()
        service.provider = Mock()
        service.provider.generate = AsyncMock(
            side_effect=LLMRefusalError(category="safety", explanation="no")
        )

        with pytest.raises(Exception):
            await service.generate_response("q")
        assert service.provider.generate.call_count == 1

    async def test_unknown_exception_text_still_triggers_a_retry(self):
        # The legacy substring heuristic survives for unmapped types.
        service = _service()
        service.provider = Mock()
        service.provider.generate = AsyncMock(
            side_effect=[RuntimeError("HTTP 429 slow down"), ("ok", 10)]
        )

        assert await service.generate_response("q") == "ok"
        assert service.provider.generate.call_count == 2


@pytest.mark.asyncio
class TestStructuredPathRetryEligibility:
    async def test_server_error_is_retried(self):
        service = _service()
        service.provider = Mock()
        service.provider.supports_native_tools = True
        service.provider.generate_structured = AsyncMock(
            side_effect=[LLMServerError("502", status_code=502), LLMResult(text="ok")]
        )

        result = await service.generate("q")
        assert result.text == "ok"
        assert service.provider.generate_structured.call_count == 2

    async def test_client_error_fails_fast(self):
        service = _service()
        service.provider = Mock()
        service.provider.supports_native_tools = True
        service.provider.generate_structured = AsyncMock(
            side_effect=LLMClientError("bad schema", status_code=400)
        )

        with pytest.raises(LLMClientError):
            await service.generate("q")
        assert service.provider.generate_structured.call_count == 1


@pytest.mark.asyncio
class TestStructuredStopReasonPolicy:
    async def test_max_tokens_marks_the_result_truncated(self):
        service = _service()
        service.provider = Mock()
        service.provider.supports_native_tools = True
        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(text="half", stop_reason="max_tokens")
        )

        result = await service.generate("q")
        assert result.truncated is True

    async def test_refusal_raises_by_default(self):
        service = _service()
        service.provider = Mock()
        service.provider.supports_native_tools = True
        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(
                stop_reason="refusal",
                stop_details={"category": "safety", "explanation": "no"},
            )
        )

        with pytest.raises(LLMRefusalError) as exc_info:
            await service.generate("q")
        assert exc_info.value.category == "safety"

    async def test_allow_refusal_returns_the_result(self):
        service = _service()
        service.provider = Mock()
        service.provider.supports_native_tools = True
        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(stop_reason="refusal")
        )

        result = await service.generate("q", allow_refusal=True)
        assert result.stop_reason == "refusal"

    async def test_end_turn_is_untouched(self):
        service = _service()
        service.provider = Mock()
        service.provider.supports_native_tools = True
        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(text="done", stop_reason="end_turn")
        )

        result = await service.generate("q")
        assert result.truncated is False
        assert result.text == "done"


@pytest.mark.asyncio
class TestExactUsageReachesTheSpan:
    async def test_structured_path_uses_the_metered_split(self):
        from core.services.llm.usage import Usage

        service = _service()
        service.provider = Mock()
        service.provider.supports_native_tools = True
        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(
                text="hi",
                usage=Usage(input_tokens=1000, output_tokens=7, cache_read_tokens=20),
            )
        )
        recorded: list[dict] = []
        with patch(
            "core.services.llm._accounting.record_genai_metrics",
            side_effect=lambda *a, **kw: recorded.append(kw),
        ):
            await service.generate("q")

        # Not "total minus an estimate of the prompt": the provider's own
        # numbers, so the output side is not inflated by a bad guess. And the
        # cached 20 stay in their own bucket rather than being folded into the
        # input figure — they bill at ~0.1x input, so counting them as fresh
        # input overcharged every ledger downstream of this split.
        assert [
            (
                kw["input_tokens"],
                kw["output_tokens"],
                kw["cache_read_tokens"],
                kw["cache_write_tokens"],
            )
            for kw in recorded
        ] == [(1000, 7, 20, 0)]

    async def test_text_path_uses_the_usage_sink(self):
        service = _service()
        captured: list[dict] = []

        async def _generate(**kwargs):
            captured.append(kwargs)
            sink = kwargs.get("usage_sink")
            if sink is not None:
                from core.services.llm.usage import Usage

                sink.append(Usage(input_tokens=900, output_tokens=3))
            return "hi", 903

        service.provider = SimpleNamespace(generate=_generate)
        recorded: list[tuple[int, int]] = []
        with patch(
            "core.services.llm._generation.record_genai_metrics",
            side_effect=lambda *a, **kw: recorded.append(
                (kw["input_tokens"], kw["output_tokens"])
            ),
        ):
            await service.generate_response("q")

        assert "usage_sink" in captured[0]
        assert recorded == [(900, 3)]


@pytest.mark.asyncio
class TestFallbackPathRefusalPolicy:
    """``allow_refusal`` must reach the prompt-coercion path too.

    The coercion path goes through the legacy text API, where the provider
    itself decides whether a refusal raises — so the flag has to be forwarded
    as a provider kwarg, exactly as the plain text path does.
    """

    @staticmethod
    def _refusing_provider(calls: list[dict]):
        async def _generate(**kwargs):
            calls.append(kwargs)
            if not kwargs.get("allow_refusal", False):
                raise LLMRefusalError(category="safety", explanation="no")
            return "I can't help with that.", 7

        return SimpleNamespace(generate=_generate, supports_native_tools=False)

    async def test_refusal_raises_on_the_coercion_path_by_default(self):
        service = _service()
        service.config.enable_native_tools = False
        service.provider = self._refusing_provider([])

        with pytest.raises(LLMRefusalError):
            await service.generate("q")

    async def test_allow_refusal_reaches_the_coercion_path(self):
        service = _service()
        service.config.enable_native_tools = False
        calls: list[dict] = []
        service.provider = self._refusing_provider(calls)

        result = await service.generate("q", allow_refusal=True)

        assert result.text == "I can't help with that."
        assert calls[-1]["allow_refusal"] is True

    async def test_allow_refusal_reaches_the_plain_text_path(self):
        service = _service()
        calls: list[dict] = []
        service.provider = self._refusing_provider(calls)

        assert await service.generate_response("q", allow_refusal=True) == (
            "I can't help with that."
        )
        assert calls[-1]["allow_refusal"] is True

    async def test_the_flag_is_absent_when_not_requested(self):
        service = _service()
        service.config.enable_native_tools = False
        calls: list[dict] = []

        async def _generate(**kwargs):
            calls.append(kwargs)
            return "fine", 3

        service.provider = SimpleNamespace(
            generate=_generate, supports_native_tools=False
        )
        await service.generate("q")
        assert "allow_refusal" not in calls[-1]


@pytest.mark.asyncio
class TestNeutralErrorsSurviveTheTextPath:
    """The text path must not re-wrap the classes callers branch on."""

    @staticmethod
    def _service_raising(exc):
        service = _service()

        async def _generate(**_kwargs):
            raise exc

        service.provider = SimpleNamespace(generate=_generate)
        return service

    async def test_refusal_propagates_with_its_type_and_detail(self):
        service = self._service_raising(
            LLMRefusalError(category="safety", explanation="no")
        )
        with pytest.raises(LLMRefusalError) as exc_info:
            await service.generate_response("q")
        assert exc_info.value.category == "safety"

    async def test_client_error_keeps_its_class_and_status(self):
        service = self._service_raising(LLMClientError("bad model", status_code=404))
        with pytest.raises(LLMClientError) as exc_info:
            await service.generate_response("q")
        assert exc_info.value.status_code == 404

    async def test_an_unrelated_failure_is_still_wrapped(self):
        service = self._service_raising(ValueError("something structural"))
        with pytest.raises(LLMProviderError) as exc_info:
            await service.generate_response("q")
        assert "Generation failed" in str(exc_info.value)


@pytest.mark.asyncio
class TestBilledRefusalsAreAccountedFor:
    """A refusal was generated and billed; the spend must reach the ledgers."""

    @staticmethod
    def _refusing_text_provider():
        async def _generate(**kwargs):
            sink = kwargs.get("usage_sink")
            if sink is not None:
                from core.services.llm.usage import Usage

                sink.append(Usage(input_tokens=500, output_tokens=9))
            raise LLMRefusalError(category="safety", explanation="no")

        return SimpleNamespace(generate=_generate, supports_native_tools=False)

    async def test_the_coercion_path_charges_a_refusal(self):
        service = _service()
        service.config.enable_native_tools = False
        service.provider = self._refusing_text_provider()

        charged: list[tuple] = []
        with patch(
            "core.orchestration.budget_context.charge_llm_cost",
            side_effect=lambda *a, **kw: charged.append(a),
        ):
            with pytest.raises(LLMRefusalError):
                await service.generate("q")

        assert charged, "a billed refusal must still charge the turn"
        # The provider's own numbers, not a guess.
        assert charged[-1][1:] == (500, 9)

    async def test_the_native_path_charges_a_refusal(self):
        service = _service()
        service.provider = Mock()
        service.provider.supports_native_tools = True
        from core.services.llm.usage import Usage

        service.provider.generate_structured = AsyncMock(
            return_value=LLMResult(
                stop_reason="refusal",
                usage=Usage(input_tokens=500, output_tokens=9),
            )
        )

        charged: list[tuple] = []
        with patch(
            "core.orchestration.budget_context.charge_llm_cost",
            side_effect=lambda *a, **kw: charged.append(a),
        ):
            with pytest.raises(LLMRefusalError):
                await service.generate("q")

        assert charged[-1][1:] == (500, 9)

    async def test_metrics_are_recorded_for_a_refused_coercion_turn(self):
        service = _service()
        service.config.enable_native_tools = False
        service.provider = self._refusing_text_provider()

        recorded: list[tuple[int, int]] = []
        with patch(
            "core.services.llm._accounting.record_genai_metrics",
            side_effect=lambda *a, **kw: recorded.append(
                (kw["input_tokens"], kw["output_tokens"])
            ),
        ):
            with pytest.raises(LLMRefusalError):
                await service.generate("q")

        assert recorded == [(500, 9)]


@pytest.mark.asyncio
class TestTextPathRefusalAccounting:
    """``generate_response`` has ~40 call sites; its refusals bill too."""

    @staticmethod
    def _service_refusing_after_metering():
        service = _service()

        async def _generate(**kwargs):
            sink = kwargs.get("usage_sink")
            if sink is not None:
                from core.services.llm.usage import Usage

                sink.append(Usage(input_tokens=500, output_tokens=9))
            raise LLMRefusalError(category="safety", explanation="no")

        service.provider = SimpleNamespace(generate=_generate)
        return service

    async def test_a_text_path_refusal_charges_exactly_once(self):
        service = self._service_refusing_after_metering()
        charged: list[tuple] = []
        with patch(
            "core.orchestration.budget_context.charge_llm_cost",
            side_effect=lambda *a, **kw: charged.append(a),
        ):
            with pytest.raises(LLMRefusalError):
                await service.generate_response("q")

        assert len(charged) == 1
        assert charged[0][1:] == (500, 9)

    async def test_the_refused_turn_reaches_the_metrics(self):
        service = self._service_refusing_after_metering()
        recorded: list[tuple[int, int]] = []
        with patch(
            "core.services.llm._accounting.record_genai_metrics",
            side_effect=lambda *a, **kw: recorded.append(
                (kw["input_tokens"], kw["output_tokens"])
            ),
        ):
            with pytest.raises(LLMRefusalError):
                await service.generate_response("q")

        assert recorded == [(500, 9)]

    async def test_the_refusal_still_propagates_with_its_detail(self):
        service = self._service_refusing_after_metering()
        with pytest.raises(LLMRefusalError) as exc_info:
            await service.generate_response("q")
        assert exc_info.value.category == "safety"
