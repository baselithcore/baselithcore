"""LLM spans and metrics speak the current OTel GenAI semantic conventions.

Pins the attribute names to the GenAI registry
(https://github.com/open-telemetry/semantic-conventions-genai): the provider
under ``gen_ai.provider.name`` with its well-known values, the deprecated
``gen_ai.system`` dual-emitted for one window, the total-including-cache
meaning of ``gen_ai.usage.input_tokens``, the dotted cache attributes and the
``string[]`` ``gen_ai.response.finish_reasons``.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest
from prometheus_client import REGISTRY

from core.observability import genai_semconv as sc
from core.services.llm._accounting import account_turn, set_usage_span_attributes
from core.services.llm._telemetry import (
    gen_ai_provider_for,
    gen_ai_provider_name,
    gen_ai_system,
    record_genai_metrics,
)
from core.services.llm.tool_calling import LLMResult
from core.services.llm.usage import Usage

pytestmark = [pytest.mark.unit]


class _Span:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class TestProviderName:
    @pytest.mark.parametrize(
        ("configured", "expected"),
        [
            ("anthropic", "anthropic"),
            ("openai", "openai"),
            ("gemini", "gcp.gemini"),
            ("bedrock", "aws.bedrock"),
            ("vertex", "gcp.vertex_ai"),
            ("mistral", "mistral_ai"),
            ("OLLAMA", "ollama"),
            ("vllm", "vllm"),
            ("something-new", "something-new"),
            (None, "unknown"),
            ("", "unknown"),
        ],
    )
    def test_well_known_values(self, configured: str | None, expected: str) -> None:
        assert gen_ai_provider_name(configured) == expected

    def test_legacy_function_name_is_an_alias(self) -> None:
        assert gen_ai_system is gen_ai_provider_name

    @pytest.mark.parametrize(
        ("backend", "expected"),
        [("api", "anthropic"), ("bedrock", "aws.bedrock"), ("vertex", "gcp.vertex_ai")],
    )
    def test_anthropic_backend_names_the_endpoint_reached(
        self, backend: str, expected: str
    ) -> None:
        config = SimpleNamespace(provider="anthropic", anthropic_backend=backend)
        assert gen_ai_provider_for(config) == expected

    def test_failover_provider_wins_over_the_configured_one(self) -> None:
        config = SimpleNamespace(provider="anthropic", anthropic_backend="bedrock")
        assert gen_ai_provider_for(config, "openai") == "openai"

    def test_a_config_double_without_the_fields_still_resolves(self) -> None:
        assert gen_ai_provider_for(object()) == "unknown"
        assert gen_ai_provider_for(Mock(provider="openai")) == "openai"

    def test_span_attributes_dual_emit_the_deprecated_key(self) -> None:
        assert sc.provider_attributes("aws.bedrock") == {
            "gen_ai.provider.name": "aws.bedrock",
            "gen_ai.system": "aws.bedrock",
        }


class TestUsageSpanAttributes:
    def test_input_tokens_include_both_cache_buckets(self) -> None:
        span = _Span()
        usage = Usage(
            input_tokens=100,
            output_tokens=40,
            cache_read_tokens=1_000,
            cache_write_tokens=300,
        )

        set_usage_span_attributes(span, usage)

        assert span.attributes == {
            "gen_ai.usage.input_tokens": 1_400,
            "gen_ai.usage.output_tokens": 40,
            "gen_ai.usage.cache_read.input_tokens": 1_000,
            "gen_ai.usage.cache_write.input_tokens": 300,
            "gen_ai.usage.cache_creation.input_tokens": 300,
        }

    def test_the_internal_record_keeps_fresh_input(self) -> None:
        """Only the span view sums: pricing still reads fresh input."""
        usage = Usage(input_tokens=100, output_tokens=40, cache_read_tokens=1_000)
        set_usage_span_attributes(_Span(), usage)
        assert usage.input_tokens == 100

    def test_no_cache_means_no_cache_attributes(self) -> None:
        span = _Span()
        set_usage_span_attributes(span, Usage(input_tokens=7, output_tokens=3))
        assert span.attributes == {
            "gen_ai.usage.input_tokens": 7,
            "gen_ai.usage.output_tokens": 3,
        }


class TestFinishReasons:
    def _service(self) -> Any:
        return SimpleNamespace(
            config=SimpleNamespace(provider="anthropic", anthropic_backend="api"),
            cost_tracker=None,
        )

    def test_stop_reason_is_a_string_array(self) -> None:
        span = _Span()
        result = LLMResult(
            text="hi",
            stop_reason="end_turn",
            tokens_used=15,
            usage=Usage(input_tokens=10, output_tokens=5),
        )
        with patch("core.services.llm._accounting.report_tokens_to_middleware"):
            account_turn(
                self._service(),
                span,
                model="claude-opus-5",
                result=result,
                input_tokens=10,
                started=time.perf_counter(),
            )

        assert span.attributes["gen_ai.response.finish_reasons"] == ["end_turn"]
        assert "gen_ai.response.finish_reason" not in span.attributes


class TestMetricLabels:
    def test_metrics_carry_the_provider_name_label(self) -> None:
        record_genai_metrics(
            "aws.bedrock",
            "semconv-label-test-model",
            input_tokens=12,
            duration_seconds=0.5,
        )
        labels = {
            "gen_ai_provider_name": "aws.bedrock",
            "gen_ai_request_model": "semconv-label-test-model",
        }
        assert (
            REGISTRY.get_sample_value(
                "gen_ai_client_token_usage_count",
                {**labels, "gen_ai_token_type": "input"},
            )
            or 0
        ) >= 1
        assert (
            REGISTRY.get_sample_value(
                "gen_ai_client_operation_duration_seconds_count",
                {**labels, "gen_ai_operation_name": "chat"},
            )
            or 0
        ) >= 1
