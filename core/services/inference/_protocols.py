"""Wire protocols of the model servers the inference services can call.

The platform's own servers speak Hugging Face TEI, but a customer that already
runs inference on its own GPUs (a DGX, an on-premises cluster) usually exposes
one of a few de-facto standard APIs instead. Each protocol here is one request
shape and one response shape; the transport (retries, budget, TLS, key) stays
in :mod:`core.services.inference._http`.

Embedding:

* ``tei`` — TEI ``POST /embed`` ``{"inputs": [...]}`` -> ``[[...], ...]``.
* ``openai`` — ``POST /embeddings`` ``{"model", "input"}`` ->
  ``{"data": [{"index", "embedding"}]}``: OpenAI, Azure OpenAI, vLLM, NVIDIA
  NIM, Infinity, Ollama's ``/v1`` and most gateways. The URL is the API root
  including ``/v1`` (as for any OpenAI client).

Rerank:

* ``tei`` — TEI ``POST /rerank`` ``{"query", "texts"}`` -> ``[{"index",
  "score"}]``.
* ``cohere`` — ``POST /rerank`` ``{"model", "query", "documents", "top_n"}`` ->
  ``{"results": [{"index", "relevance_score"}]}``: Cohere, Jina, vLLM,
  Infinity, Xinference. URL including ``/v1`` where the server has one.
* ``nim`` — NVIDIA NIM reranking ``POST /ranking`` ``{"model", "query":
  {"text"}, "passages": [{"text"}]}`` -> ``{"rankings": [{"index",
  "logit"}]}``; logits go through a sigmoid so scores live in [0, 1] like
  TEI's.
"""

from __future__ import annotations

import math
from typing import Any

from core.config.inference import EmbeddingApi, RerankApi
from core.services.inference.errors import InferenceError

EMBED_PATHS: dict[str, str] = {"tei": "/embed", "openai": "/embeddings"}
RERANK_PATHS: dict[str, str] = {
    "tei": "/rerank",
    "cohere": "/rerank",
    "nim": "/ranking",
}


def embed_payload(api: str, model: str, texts: list[str]) -> dict[str, Any]:
    """Request body for one batch of ``texts``."""
    if api == "openai":
        return {"model": model, "input": texts, "encoding_format": "float"}
    return {"inputs": texts, "normalize": True, "truncate": True}


def parse_embeddings(api: str, body: Any, count: int) -> list[list[float]]:
    """Vectors in input order, or :class:`InferenceError` on a malformed body."""
    if api == "openai":
        rows = body.get("data") if isinstance(body, dict) else None
        if not isinstance(rows, list):
            raise InferenceError("embeddings response has no 'data' list")
        try:
            ordered = sorted(rows, key=lambda row: int(row["index"]))
            vectors = [row["embedding"] for row in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise InferenceError(f"malformed embeddings row: {exc}") from exc
    else:
        vectors = body
    if not isinstance(vectors, list) or len(vectors) != count:
        got = len(vectors) if isinstance(vectors, list) else "a non-list"
        raise InferenceError(
            f"embedding server returned {got} vectors for {count} inputs"
        )
    return [[float(x) for x in vec] for vec in vectors]


def rerank_payload(
    api: str, model: str, query: str, texts: list[str]
) -> dict[str, Any]:
    """Request body scoring ``texts`` against ``query``."""
    if api == "cohere":
        return {"model": model, "query": query, "documents": texts, "top_n": len(texts)}
    if api == "nim":
        return {
            "model": model,
            "query": {"text": query},
            "passages": [{"text": t} for t in texts],
            "truncate": "END",
        }
    return {"query": query, "texts": texts, "raw_scores": False, "truncate": True}


def _rows_and_key(api: str, body: Any) -> tuple[Any, str]:
    if api == "cohere":
        return (
            body.get("results") if isinstance(body, dict) else None
        ), "relevance_score"
    if api == "nim":
        return (body.get("rankings") if isinstance(body, dict) else None), "logit"
    return body, "score"


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def parse_scores(api: str, body: Any, count: int) -> list[float]:
    """One score per input text, in input order."""
    rows, key = _rows_and_key(api, body)
    if not isinstance(rows, list):
        raise InferenceError("rerank response has no result list")
    scores = [0.0] * count
    seen: set[int] = set()
    for row in rows:
        try:
            idx = int(row["index"])
            value = float(row[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise InferenceError(f"malformed rerank row: {exc}") from exc
        if not 0 <= idx < count:
            raise InferenceError(f"rerank returned out-of-range index {idx}")
        scores[idx] = _sigmoid(value) if api == "nim" else value
        seen.add(idx)
    if len(seen) != count:
        raise InferenceError(f"rerank scored {len(seen)} of {count} texts")
    return scores


__all__ = [
    "EMBED_PATHS",
    "RERANK_PATHS",
    "EmbeddingApi",
    "RerankApi",
    "embed_payload",
    "parse_embeddings",
    "parse_scores",
    "rerank_payload",
]
