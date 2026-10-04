"""The remote embedder keeps the index's geometry and the query/document roles.

* A served model that is not the requested one is never substituted silently:
  its vectors would land in (or be searched against) an index built by
  another model. Substitution needs matching dimensions *and* an explicit
  opt-in; a dimension mismatch with ``VECTORSTORE_EMBEDDING_DIM`` fails fast.
* The search side goes through ``encode_query`` (sentence-transformers' own
  convention), so ``BASELITH_EMBEDDING_QUERY_PREFIX`` applies to queries and
  ``..._DOCUMENT_PREFIX`` only to documents; the cache keys on the role and
  the prefix, so changing a prefix never serves a stale vector.
* Reranking past ``BASELITH_RERANK_MAX_CANDIDATES`` scores every passage.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from core.config import inference
from core.config.inference import EmbeddingConfig, RerankConfig
from core.nlp import _remote
from core.services.inference.errors import InferenceConfigError


class _Bridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.rerank_calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(("document", list(texts)))
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(("query", list(texts)))
        return [[0.0, 1.0, 0.0] for _ in texts]

    def rerank(
        self, query: str, texts: list[str], top_k: int
    ) -> list[tuple[int, float]]:
        self.rerank_calls.append(list(texts))
        assert len(texts) <= 2, "the service would drop these"
        return sorted(
            ((i, float(t)) for i, t in enumerate(texts)), key=lambda p: -p[1]
        )[:top_k]


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> _Bridge:
    fake = _Bridge()
    monkeypatch.setattr("core.services.inference.get_sync_inference", lambda: fake)
    return fake


def _configure(
    monkeypatch: pytest.MonkeyPatch, *, index_dim: int = 3, **kw: Any
) -> None:
    base: dict[str, Any] = {"url": "http://tei-embed", "model": "served", "dim": 3}
    monkeypatch.setattr(inference, "_embedding", EmbeddingConfig(**{**base, **kw}))
    monkeypatch.setattr(_remote, "_index_dim", lambda: index_dim)


# --- substitution / dimension contract -------------------------------------


def test_matching_model_with_matching_dim_goes_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch)
    assert _remote.use_remote_embedder("served") is True


def test_dim_mismatch_with_the_index_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, index_dim=1024)
    with pytest.raises(InferenceConfigError, match="VECTORSTORE_EMBEDDING_DIM"):
        _remote.use_remote_embedder("served")


def test_substitution_is_refused_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch)
    monkeypatch.setattr(_remote, "local_runtime_installed", lambda: False)
    with pytest.raises(InferenceConfigError, match="ALLOW_MODEL_SUBSTITUTION"):
        _remote.use_remote_embedder("requested")


def test_substitution_needs_opt_in_and_matching_dims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_remote, "local_runtime_installed", lambda: False)
    _configure(monkeypatch, allow_model_substitution=True)
    assert _remote.use_remote_embedder("requested") is True
    _configure(monkeypatch, allow_model_substitution=True, index_dim=384)
    with pytest.raises(InferenceConfigError):
        _remote.use_remote_embedder("requested")


def test_a_local_runtime_still_serves_another_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, index_dim=384)  # irrelevant: the local model is used
    monkeypatch.setattr(_remote, "local_runtime_installed", lambda: True)
    assert _remote.use_remote_embedder("requested") is False


# --- query / document roles --------------------------------------------------


def test_remote_model_routes_roles(
    monkeypatch: pytest.MonkeyPatch, bridge: _Bridge
) -> None:
    _configure(monkeypatch, query_prefix="q: ", document_prefix="d: ")
    model = _remote.RemoteEmbeddingModel()
    assert model.prompts == {"query": "q: ", "document": "d: "}
    model.encode(["doc"])
    model.encode_query("question")
    model.encode(["question"], prompt_name="query")  # the ST spelling
    model.encode_document(["doc"])
    assert [role for role, _ in bridge.calls] == [
        "document",
        "query",
        "query",
        "document",
    ]
    assert model.encode_query("x").shape == (3,)


async def test_cached_embedder_query_side_and_cache_scope(
    monkeypatch: pytest.MonkeyPatch, bridge: _Bridge
) -> None:
    from core.nlp.models import CachedEmbedder
    from core.nlp.roles import aencode_query

    _configure(monkeypatch, query_prefix="q: ")
    emb = CachedEmbedder(_remote.RemoteEmbeddingModel(), cache_backend="memory")
    doc = await emb.encode("same text")
    query = await aencode_query(emb, "same text")
    assert list(doc) == [1.0, 0.0, 0.0]
    assert list(query) == [0.0, 1.0, 0.0], "the query was served the document vector"
    await aencode_query(emb, "same text")  # cached under its own role
    assert bridge.calls == [("document", ["same text"]), ("query", ["same text"])]

    # A new query prefix must not reuse the vector cached under the old one.
    _configure(monkeypatch, query_prefix="instruct: ")
    emb2 = CachedEmbedder(
        _remote.RemoteEmbeddingModel(),
        cache=emb._cache,  # shared, as Redis is
    )
    await aencode_query(emb2, "same text")
    assert bridge.calls[-1] == ("query", ["same text"])
    assert len(bridge.calls) == 3


async def test_aencode_query_falls_back_to_encode_for_plain_embedders() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from core.nlp.roles import aencode_query

    async_mock = MagicMock()
    async_mock.encode = AsyncMock(return_value=[1.0])
    assert await aencode_query(async_mock, "q") == [1.0]
    sync_mock = MagicMock()
    sync_mock.encode = MagicMock(return_value=np.array([2.0]))
    assert list(await aencode_query(sync_mock, "q")) == [2.0]


# --- rerank past the candidate cap ------------------------------------------


def test_predict_scores_every_passage_past_the_cap(
    monkeypatch: pytest.MonkeyPatch, bridge: _Bridge
) -> None:
    monkeypatch.setattr(
        inference, "_rerank", RerankConfig(url="http://rr", max_candidates=2)
    )
    pairs = [("q", str(s)) for s in (5, 1, 4, 2, 3)]
    scores = _remote.RemoteCrossEncoder().predict(pairs)
    assert scores.tolist() == [5.0, 1.0, 4.0, 2.0, 3.0]
    assert bridge.rerank_calls == [["5", "1"], ["4", "2"], ["3"]]
