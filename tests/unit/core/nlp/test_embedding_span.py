"""Gen AI telemetry around the embedding provider.

Embeddings were the one Gen AI call in the framework with no span at all: a
retrieval request showed the vector search and the completion, with the model
inference that produced the query vector missing from the trace entirely.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from core.config.vectorstore import VectorStoreConfig
from core.nlp.models import EMBEDDING_OPERATION, CachedEmbedder

pytestmark = [pytest.mark.unit]


def _token_usage(enabled: bool):
    """Toggle VECTORSTORE_EMBEDDING_TOKEN_USAGE_ENABLED for one test."""
    return patch(
        "core.nlp.models.get_vectorstore_config",
        return_value=VectorStoreConfig(embedding_token_usage_enabled=enabled),
    )


@pytest.fixture
def otel_sdk(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda *a, **k: provider.get_tracer("t"))
    monkeypatch.setattr("core.observability.tracing._otel_active", lambda: True)
    yield exporter


def _model(name: str = "all-MiniLM-L6-v2", tokens: int | None = 7):
    model = MagicMock()
    model.encode.side_effect = lambda texts, **kw: np.array(
        [[0.1, 0.2]] * (1 if isinstance(texts, str) else len(texts))
    )
    model.get_sentence_embedding_dimension.return_value = 2
    model.__class__.__name__ = "SentenceTransformer"
    model.model_card_data = MagicMock(base_model=name)
    if tokens is None:
        model.tokenizer = None
    else:
        model.tokenizer = MagicMock(
            return_value={"input_ids": [list(range(tokens))]},
        )
    return model


def _embedder(model=None):
    """A CachedEmbedder with caching off, so encode() always hits the model."""
    embedder = CachedEmbedder(model or _model(), cache=None)
    embedder._cache = None
    return embedder


@pytest.mark.asyncio
class TestEmbeddingSpan:
    async def test_a_span_is_emitted(self, otel_sdk):
        await _embedder().encode("hello")
        names = [span.name for span in otel_sdk.get_finished_spans()]
        assert any(name.startswith(EMBEDDING_OPERATION) for name in names)

    async def test_operation_name_follows_the_semconv(self, otel_sdk):
        await _embedder().encode("hello")
        (span,) = otel_sdk.get_finished_spans()
        assert span.attributes["gen_ai.operation.name"] == "embeddings"

    async def test_request_model_is_recorded(self, otel_sdk):
        await _embedder(_model(name="bge-small")).encode("hello")
        (span,) = otel_sdk.get_finished_spans()
        assert span.attributes["gen_ai.request.model"] == "bge-small"

    async def test_input_count_is_recorded(self, otel_sdk):
        await _embedder().encode(["a", "b", "c"])
        (span,) = otel_sdk.get_finished_spans()
        assert span.attributes["gen_ai.baselith.input_count"] == 3

    async def test_token_usage_when_enabled_and_the_tokenizer_exposes_it(
        self, otel_sdk
    ):
        with _token_usage(True):
            await _embedder(_model(tokens=11)).encode("hello")
        (span,) = otel_sdk.get_finished_spans()
        assert span.attributes["gen_ai.usage.input_tokens"] == 11

    async def test_token_usage_is_off_by_default(self, otel_sdk):
        """It costs a second full tokenizer pass, so it is opt-in."""
        with _token_usage(False):
            await _embedder(_model(tokens=11)).encode("hello")
        (span,) = otel_sdk.get_finished_spans()
        assert "gen_ai.usage.input_tokens" not in span.attributes

    async def test_the_tokenizer_is_not_called_when_disabled(self, otel_sdk):
        model = _model(tokens=11)
        with _token_usage(False):
            await _embedder(model).encode("hello")
        model.tokenizer.assert_not_called()

    async def test_no_token_attribute_without_a_tokenizer(self, otel_sdk):
        with _token_usage(True):
            await _embedder(_model(tokens=None)).encode("hello")
        (span,) = otel_sdk.get_finished_spans()
        assert "gen_ai.usage.input_tokens" not in span.attributes

    async def test_a_broken_tokenizer_does_not_break_encoding(self, otel_sdk):
        model = _model()
        model.tokenizer.side_effect = RuntimeError("tokenizer exploded")
        with _token_usage(True):
            result = await _embedder(model).encode("hello")
        assert result is not None

    async def test_embedding_is_returned_unchanged(self, otel_sdk):
        result = await _embedder().encode("hello")
        assert np.allclose(np.asarray(result), np.array([0.1, 0.2]))

    async def test_the_span_is_current_so_nested_work_parents_to_it(
        self, otel_sdk, monkeypatch
    ):
        """``Tracer.start_span`` is the house API and has no
        ``start_as_current_span`` twin — but it enters the OTel tracer's
        ``start_as_current_span`` internally, so the embeddings span *is* the
        current OTel span and work started inside it nests underneath.

        The real ``run_inference`` is used: it hands the blocking model call to
        a thread pool but copies the caller's ``contextvars`` across the hop, so
        the OTel context survives and the nested span parents correctly. (It did
        not always: a bare ``run_in_executor`` orphaned every span opened in the
        worker — see ``tests/unit/core/utils/test_inference_executor.py``.)
        """
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(otel_sdk))
        monkeypatch.setattr(
            trace, "get_tracer", lambda *a, **k: provider.get_tracer("t")
        )

        model = _model()

        def _encode(texts, **kw):
            with provider.get_tracer("t").start_as_current_span("nested-work"):
                pass
            return np.array([[0.1, 0.2]])

        model.encode.side_effect = _encode
        await _embedder(model).encode("hello")

        spans = {s.name: s for s in otel_sdk.get_finished_spans()}
        parent = next(s for n, s in spans.items() if n.startswith(EMBEDDING_OPERATION))
        child = spans["nested-work"]
        assert child.parent is not None
        assert child.parent.span_id == parent.context.span_id
        assert child.context.trace_id == parent.context.trace_id

    async def test_model_failure_marks_the_span(self, otel_sdk):
        model = _model()
        model.encode.side_effect = RuntimeError("cuda oom")
        with pytest.raises(RuntimeError):
            await _embedder(model).encode("hello")
        (span,) = otel_sdk.get_finished_spans()
        assert span.status.status_code is trace.StatusCode.ERROR


@pytest.mark.asyncio
class TestEmbeddingMetrics:
    async def test_duration_and_tokens_are_observed(self):
        from prometheus_client import REGISTRY

        labels = {
            "gen_ai_system": "sentence_transformers",
            "gen_ai_request_model": "metrics-model",
        }
        before = (
            REGISTRY.get_sample_value(
                "gen_ai_client_operation_duration_seconds_count",
                {**labels, "gen_ai_operation_name": "embeddings"},
            )
            or 0.0
        )
        with _token_usage(True):
            await _embedder(_model(name="metrics-model", tokens=5)).encode("hello")
        after = REGISTRY.get_sample_value(
            "gen_ai_client_operation_duration_seconds_count",
            {**labels, "gen_ai_operation_name": "embeddings"},
        )
        assert after == before + 1
        assert (
            REGISTRY.get_sample_value(
                "gen_ai_client_token_usage_sum",
                {**labels, "gen_ai_token_type": "input"},
            )
            >= 5
        )

    async def test_metric_failure_never_breaks_encoding(self, monkeypatch):
        monkeypatch.setattr(
            "core.nlp.models.GEN_AI_OPERATION_DURATION",
            MagicMock(labels=MagicMock(side_effect=RuntimeError("registry down"))),
        )
        assert await _embedder().encode("hello") is not None
