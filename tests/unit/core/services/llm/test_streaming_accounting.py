"""Token accounting for the streaming path.

The provider yields a *cumulative* count that already includes the prompt, and
the service also books the prompt estimate before opening the stream — so the
prompt used to be charged twice, and the span only ever carried the output
half of the semconv pair.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.services.llm.service import LLMService

PROMPT_ESTIMATE = 100


class _Span:
    def __init__(self):
        self.attributes: dict = {}

    def set_attribute(self, key, value):
        self.attributes[key] = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Tracer:
    def __init__(self, span):
        self._span = span

    def start_span(self, *_args, **_kwargs):
        return self._span


def _service():
    with patch("core.services.llm.service.get_llm_config") as mock_config:
        mock_config.return_value = Mock(
            provider="ollama",
            model="m",
            enable_cache=False,
            fallback_chain="",
            max_concurrent_requests=0,
        )
        return LLMService()


async def _run_stream(service, span):
    reported: list[tuple[int, str]] = []
    with (
        patch("core.observability.get_tracer", return_value=_Tracer(span)),
        patch(
            "core.services.llm._streaming.report_tokens_to_middleware",
            side_effect=lambda tokens, model="": reported.append((tokens, model)),
        ),
        patch(
            "core.services.llm._streaming.estimate_tokens_async",
            new=_fixed_estimate,
        ),
    ):
        chunks = [c async for c in service.generate_response_stream("hello")]
    return chunks, reported


async def _fixed_estimate(*_args, **_kwargs):
    """Pin the prompt estimate so the accounting assertions are exact."""
    return PROMPT_ESTIMATE


@pytest.mark.asyncio
class TestStreamTokenAccounting:
    async def test_prompt_tokens_are_reported_once(self):
        service = _service()

        async def _stream(prompt, model, **kwargs):
            # Cumulative, prompt included — the provider contract.
            yield "a", PROMPT_ESTIMATE + 5
            yield "b", PROMPT_ESTIMATE + 9

        service.provider = SimpleNamespace(generate_stream=_stream)
        span = _Span()
        chunks, reported = await _run_stream(service, span)

        assert chunks == ["a", "b"]
        # 100 (prompt, booked once) + 5 + 4 = 109, not 209.
        assert sum(tokens for tokens, _ in reported) == PROMPT_ESTIMATE + 9

    async def test_exact_usage_from_a_sink_beats_the_estimate(self):
        """A provider that meters the stream must not be second-guessed."""
        from core.services.llm.usage import Usage

        service = _service()
        captured: list[dict] = []

        async def _stream(prompt, model, **kwargs):
            captured.append(kwargs)
            sink = kwargs.get("usage_sink")
            if sink is not None:
                sink.append(Usage(input_tokens=900, output_tokens=7))
            yield "a", 999

        service.provider = SimpleNamespace(generate_stream=_stream)
        span = _Span()
        await _run_stream(service, span)

        # The sink reached the provider...
        assert "usage_sink" in captured[0]
        # ...and its numbers, not `accumulated - estimate(prompt)`, are what
        # the span reports.
        assert span.attributes["gen_ai.usage.input_tokens"] == 900
        assert span.attributes["gen_ai.usage.output_tokens"] == 7

    async def test_span_carries_both_semconv_token_attributes(self):
        service = _service()

        async def _stream(prompt, model, **kwargs):
            yield "a", PROMPT_ESTIMATE + 42

        service.provider = SimpleNamespace(generate_stream=_stream)
        span = _Span()
        await _run_stream(service, span)

        # No sink was filled, so the estimate-based split stands.
        assert span.attributes["gen_ai.usage.input_tokens"] == PROMPT_ESTIMATE
        assert span.attributes["gen_ai.usage.output_tokens"] == 42

    async def test_a_provider_correction_never_reports_negative_tokens(self):
        # The exact count can come in *below* the running estimate when the
        # provider's terminal usage event lands.
        service = _service()

        async def _stream(prompt, model, **kwargs):
            yield "a", PROMPT_ESTIMATE + 50
            yield "", PROMPT_ESTIMATE + 10  # correction downward

        service.provider = SimpleNamespace(generate_stream=_stream)
        span = _Span()
        _, reported = await _run_stream(service, span)

        assert all(tokens >= 0 for tokens, _ in reported)
        # The span carries what the provider last reported, not the
        # high-water mark of the running estimate.
        assert span.attributes["gen_ai.usage.output_tokens"] == 10
