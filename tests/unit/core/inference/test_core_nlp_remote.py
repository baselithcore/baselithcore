"""core.nlp routes its embedder/reranker to the inference services when configured."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from core.config import inference
from core.config.inference import EmbeddingConfig, RerankConfig
from core.nlp import _remote


class _Bridge:
    def __init__(self) -> None:
        self.rerank_calls: list[tuple[str, list[str]]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    def rerank(
        self, query: str, texts: list[str], top_k: int
    ) -> list[tuple[int, float]]:
        self.rerank_calls.append((query, list(texts)))
        ranked = sorted(enumerate(float(len(t)) for t in texts), key=lambda p: -p[1])
        return ranked[:top_k]


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> _Bridge:
    fake = _Bridge()
    monkeypatch.setattr("core.services.inference.get_sync_inference", lambda: fake)
    return fake


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        inference,
        "_embedding",
        EmbeddingConfig(url="http://tei-embed", model="m-embed", dim=3),
    )
    monkeypatch.setattr(
        inference, "_rerank", RerankConfig(url="http://tei-rerank", model="m-rerank")
    )


def test_remote_only_with_backend_url_and_matching_model(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    monkeypatch.setattr(_remote, "local_runtime_installed", lambda: True)
    assert _remote.use_remote_embedder("m-embed") is True
    assert _remote.use_remote_embedder("other-model") is False  # local can serve it
    monkeypatch.setattr(
        inference, "_embedding", EmbeddingConfig(url=None, model="m-embed")
    )
    assert _remote.use_remote_embedder("m-embed") is False  # no server configured
    monkeypatch.setattr(
        inference,
        "_embedding",
        EmbeddingConfig(backend="local", url="http://x", model="m-embed"),
    )
    assert _remote.use_remote_embedder("m-embed") is False  # explicit local


def test_without_a_local_runtime_the_served_model_substitutes(
    monkeypatch: pytest.MonkeyPatch, configured: None
) -> None:
    monkeypatch.setattr(_remote, "local_runtime_installed", lambda: False)
    assert _remote.use_remote_reranker("cross-encoder/ms-marco-MiniLM-L-6-v2") is True


def test_embedding_model_mirrors_sentence_transformer_shapes(
    bridge: _Bridge, configured: None
) -> None:
    model = _remote.RemoteEmbeddingModel()
    assert model.get_sentence_embedding_dimension() == 3
    one = model.encode("abcd")
    assert isinstance(one, np.ndarray) and one.tolist() == [4.0, 1.0, 0.0]
    many = model.encode(["a", "bb"])
    assert many.shape == (2, 3)
    as_list = model.encode(["a"], convert_to_numpy=False)
    assert isinstance(as_list, list) and as_list[0].tolist() == [1.0, 1.0, 0.0]


def test_cross_encoder_scores_in_input_order_grouped_by_query(
    bridge: _Bridge, configured: None
) -> None:
    pairs = [("q1", "aaa"), ("q2", "b"), ("q1", "a"), ("q2", "bbbb")]
    scores = _remote.RemoteCrossEncoder().predict(pairs)
    assert scores.tolist() == [3.0, 1.0, 1.0, 4.0]
    assert bridge.rerank_calls == [("q1", ["aaa", "a"]), ("q2", ["b", "bbbb"])]


def test_get_embedder_and_get_reranker_go_remote(
    monkeypatch: pytest.MonkeyPatch, bridge: _Bridge, configured: None
) -> None:
    from core.nlp import models

    models.get_embedder.cache_clear()
    models.get_reranker.cache_clear()

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("local model must not be built")

    monkeypatch.setattr(models, "_model_classes", _boom)
    try:
        embedder = models.get_embedder("m-embed")
        assert isinstance(embedder.model, _remote.RemoteEmbeddingModel)
        reranker = models.get_reranker("m-rerank")
        assert isinstance(reranker, _remote.RemoteCrossEncoder)
    finally:
        models.get_embedder.cache_clear()
        models.get_reranker.cache_clear()


async def test_cached_embedder_round_trip_through_the_remote_model(
    bridge: _Bridge, configured: None
) -> None:
    from core.nlp.models import CachedEmbedder

    emb = CachedEmbedder(_remote.RemoteEmbeddingModel(), cache_backend="memory")
    vec = await emb.encode("hello")
    assert list(vec) == [5.0, 1.0, 0.0]
    again = await emb.encode(["hello", "hi"])
    assert np.asarray(again).shape == (2, 3)


async def test_retrieval_reranker_uses_the_service_without_sentence_transformers(
    monkeypatch: pytest.MonkeyPatch, bridge: _Bridge, configured: None
) -> None:
    from core.models.domain import Document, SearchResult
    from core.services.retrieval import reranker as rr_mod

    monkeypatch.setattr(rr_mod, "CrossEncoder", None)
    monkeypatch.setattr(rr_mod, "use_remote_reranker", lambda _name: True)
    rr = rr_mod.Reranker("m-rerank")
    docs = [
        SearchResult(document=Document(id=str(i), content=c), score=0.0)
        for i, c in enumerate(["a", "abcd", "ab"])
    ]
    out = await rr.rerank("q", docs, top_k=2)
    assert [r.document.content for r in out] == ["abcd", "ab"]
