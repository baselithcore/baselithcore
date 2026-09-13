"""Bounded tenant labels and trace exemplars on the Prometheus metrics."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.openmetrics.exposition import (
    generate_latest as generate_openmetrics,
)

from core.config.observability import ObservabilityConfig, reset_observability_config
from core.observability.metric_context import (
    TENANT_OTHER,
    TENANT_UNKNOWN,
    ExemplarHistogram,
    TenantLabeledCounter,
    _ExemplarChild,
    current_exemplar,
    exemplar_is_acceptable,
    resolve_tenant_label,
)

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _clean_config():
    reset_observability_config()
    yield
    reset_observability_config()


def _config(**kwargs):
    """Patch the observability config singleton for one test."""
    return patch(
        "core.observability.metric_context.get_observability_config",
        return_value=ObservabilityConfig(**kwargs),
    )


def _span_context(trace_id: int = 0x1234, sampled: bool = True, valid: bool = True):
    """A fake OTel span whose context reports the given trace id/flags."""
    ctx = MagicMock()
    ctx.trace_id = trace_id
    ctx.is_valid = valid
    ctx.trace_flags.sampled = sampled
    span = MagicMock()
    span.get_span_context.return_value = ctx
    return span


# --------------------------------------------------------------------------- #
# resolve_tenant_label — bounded cardinality
# --------------------------------------------------------------------------- #


class TestResolveTenantLabel:
    def test_allowlisted_tenant_is_kept(self):
        with _config(metrics_tenant_label_allowlist={"acme", "globex"}):
            assert resolve_tenant_label("acme") == "acme"

    def test_unlisted_tenant_collapses_to_other(self):
        with _config(metrics_tenant_label_allowlist={"acme"}):
            assert resolve_tenant_label("someone-else") == TENANT_OTHER

    def test_empty_allowlist_collapses_everything(self):
        """The default is fail-closed on cardinality: no tenant is broken out."""
        with _config():
            assert resolve_tenant_label("acme") == TENANT_OTHER

    def test_missing_tenant_is_unknown(self):
        with (
            _config(metrics_tenant_label_allowlist={"acme"}),
            patch("core.context.get_current_tenant_id", return_value=""),
        ):
            assert resolve_tenant_label("") == TENANT_UNKNOWN
            assert resolve_tenant_label(None) == TENANT_UNKNOWN

    def test_falls_back_to_ambient_tenant_context(self):
        with (
            _config(metrics_tenant_label_allowlist={"acme"}),
            patch("core.context.get_current_tenant_id", return_value="acme"),
        ):
            assert resolve_tenant_label() == "acme"

    def test_tenant_context_error_never_propagates(self):
        with (
            _config(metrics_tenant_label_allowlist={"acme"}),
            patch("core.context.get_current_tenant_id", side_effect=RuntimeError("x")),
        ):
            assert resolve_tenant_label() == TENANT_UNKNOWN


# --------------------------------------------------------------------------- #
# TenantLabeledCounter — the existing two-label call sites keep working
# --------------------------------------------------------------------------- #


class TestTenantLabeledCounter:
    def _counter(self):
        registry = CollectorRegistry()
        counter = Counter(
            "demo_cost_usd_total",
            "demo",
            ["gen_ai_system", "gen_ai_request_model", "tenant"],
            registry=registry,
        )
        return registry, TenantLabeledCounter(counter)

    def test_positional_call_without_tenant_backfills_the_label(self):
        registry, wrapped = self._counter()
        with (
            _config(metrics_tenant_label_allowlist={"acme"}),
            patch("core.context.get_current_tenant_id", return_value="acme"),
        ):
            wrapped.labels("anthropic", "claude-opus-5").inc(1.5)
        assert (
            registry.get_sample_value(
                "demo_cost_usd_total",
                {
                    "gen_ai_system": "anthropic",
                    "gen_ai_request_model": "claude-opus-5",
                    "tenant": "acme",
                },
            )
            == 1.5
        )

    def test_keyword_call_without_tenant_backfills_the_label(self):
        registry, wrapped = self._counter()
        with _config():
            wrapped.labels(gen_ai_system="ollama", gen_ai_request_model="llama3").inc(
                2.0
            )
        assert (
            registry.get_sample_value(
                "demo_cost_usd_total",
                {
                    "gen_ai_system": "ollama",
                    "gen_ai_request_model": "llama3",
                    "tenant": TENANT_OTHER,
                },
            )
            == 2.0
        )

    def test_explicit_tenant_is_still_bounded(self):
        registry, wrapped = self._counter()
        with _config(metrics_tenant_label_allowlist={"acme"}):
            wrapped.labels("anthropic", "claude-opus-5", "rogue-tenant").inc(1.0)
        assert (
            registry.get_sample_value(
                "demo_cost_usd_total",
                {
                    "gen_ai_system": "anthropic",
                    "gen_ai_request_model": "claude-opus-5",
                    "tenant": TENANT_OTHER,
                },
            )
            == 1.0
        )

    def test_delegates_unknown_attributes_to_the_metric(self):
        _registry, wrapped = self._counter()
        assert wrapped._name == "demo_cost_usd"


# --------------------------------------------------------------------------- #
# Exemplars
# --------------------------------------------------------------------------- #


class TestCurrentExemplar:
    def test_none_without_a_recording_span(self):
        with (
            _config(),
            patch("opentelemetry.trace.get_current_span", return_value=None),
        ):
            assert current_exemplar() is None

    def test_none_for_an_unsampled_span(self):
        with (
            _config(),
            patch(
                "opentelemetry.trace.get_current_span",
                return_value=_span_context(sampled=False),
            ),
        ):
            assert current_exemplar() is None

    def test_none_for_an_invalid_span_context(self):
        with (
            _config(),
            patch(
                "opentelemetry.trace.get_current_span",
                return_value=_span_context(valid=False),
            ),
        ):
            assert current_exemplar() is None

    def test_trace_id_rendered_as_32_hex_chars(self):
        with (
            _config(),
            patch(
                "opentelemetry.trace.get_current_span",
                return_value=_span_context(trace_id=0xABC),
            ),
        ):
            assert current_exemplar() == {
                "trace_id": "00000000000000000000000000000abc"
            }

    def test_disabled_by_configuration(self):
        with (
            _config(metrics_exemplars_enabled=False),
            patch(
                "opentelemetry.trace.get_current_span",
                return_value=_span_context(),
            ),
        ):
            assert current_exemplar() is None


class TestExemplarHistogram:
    def _histogram(self):
        registry = CollectorRegistry()
        histogram = Histogram(
            "demo_latency_seconds",
            "demo",
            ["method", "route"],
            registry=registry,
        )
        return registry, ExemplarHistogram(histogram)

    def test_observation_carries_the_trace_exemplar(self):
        registry, wrapped = self._histogram()
        with (
            _config(),
            patch(
                "opentelemetry.trace.get_current_span",
                return_value=_span_context(trace_id=0xFEED),
            ),
        ):
            wrapped.labels(method="GET", route="/x").observe(0.02)
        exposition = generate_openmetrics(registry).decode()
        assert "trace_id=" in exposition
        assert "0000000000000000000000000000feed" in exposition

    def test_observation_without_a_span_still_records(self):
        registry, wrapped = self._histogram()
        with (
            _config(),
            patch("opentelemetry.trace.get_current_span", return_value=None),
        ):
            wrapped.labels(method="GET", route="/x").observe(0.02)
        assert (
            registry.get_sample_value(
                "demo_latency_seconds_sum", {"method": "GET", "route": "/x"}
            )
            == 0.02
        )

    def test_positional_labels_are_supported(self):
        registry, wrapped = self._histogram()
        with (
            _config(),
            patch("opentelemetry.trace.get_current_span", return_value=None),
        ):
            wrapped.labels("POST", "/y").observe(1.0)
        assert (
            registry.get_sample_value(
                "demo_latency_seconds_count", {"method": "POST", "route": "/y"}
            )
            == 1.0
        )

    def test_a_rejected_exemplar_is_counted_exactly_once(self):
        """The regression: prometheus_client increments the bucket and the sum
        *before* it validates the exemplar, so observing again in a fallback
        counted one observation twice."""
        registry, wrapped = self._histogram()
        with (
            _config(),
            patch(
                "core.observability.metric_context.current_exemplar",
                return_value={"trace_id": "z" * 200},  # over the 128-rune cap
            ),
        ):
            wrapped.labels(method="GET", route="/x").observe(0.5)
        labels = {"method": "GET", "route": "/x"}
        assert registry.get_sample_value("demo_latency_seconds_count", labels) == 1.0
        assert registry.get_sample_value("demo_latency_seconds_sum", labels) == 0.5

    def test_an_accepted_exemplar_is_also_counted_once(self):
        registry, wrapped = self._histogram()
        with (
            _config(),
            patch(
                "opentelemetry.trace.get_current_span",
                return_value=_span_context(trace_id=0xFEED),
            ),
        ):
            wrapped.labels(method="GET", route="/x").observe(0.5)
        labels = {"method": "GET", "route": "/x"}
        assert registry.get_sample_value("demo_latency_seconds_count", labels) == 1.0
        assert "trace_id=" in generate_openmetrics(registry).decode()

    def test_oversized_exemplars_are_rejected_up_front(self):
        assert exemplar_is_acceptable({"trace_id": "a" * 32}) is True
        assert exemplar_is_acceptable({"trace_id": "a" * 200}) is False
        assert exemplar_is_acceptable({}) is False

    def test_a_client_without_exemplar_support_still_records(self):
        """An older prometheus_client takes no ``exemplar`` keyword at all."""
        recorded: list = []

        class _NoExemplarChild:
            def observe(self, amount):
                recorded.append(amount)

        with (
            _config(),
            patch(
                "opentelemetry.trace.get_current_span",
                return_value=_span_context(),
            ),
        ):
            _ExemplarChild(_NoExemplarChild()).observe(0.25)
        assert recorded == [0.25]

    def test_delegates_unknown_attributes_to_the_metric(self):
        _registry, wrapped = self._histogram()
        assert wrapped._name == "demo_latency_seconds"


# --------------------------------------------------------------------------- #
# Wiring into the shipped metric registry
# --------------------------------------------------------------------------- #


class TestShippedMetricsAreInstrumented:
    def test_gen_ai_cost_counter_carries_a_tenant_label(self):
        from core.observability.metrics import GEN_AI_COST_USD

        assert isinstance(GEN_AI_COST_USD, TenantLabeledCounter)
        assert "tenant" in GEN_AI_COST_USD._labelnames

    def test_http_and_genai_histograms_are_exemplar_aware(self):
        from core.observability.metrics import (
            GEN_AI_OPERATION_DURATION,
            GEN_AI_TOKEN_USAGE,
            HTTP_REQUEST_DURATION_SECONDS,
        )

        for metric in (
            HTTP_REQUEST_DURATION_SECONDS,
            GEN_AI_OPERATION_DURATION,
            GEN_AI_TOKEN_USAGE,
        ):
            assert isinstance(metric, ExemplarHistogram)
