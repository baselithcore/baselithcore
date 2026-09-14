"""
NLP Models

Provides embedder and reranker model loading with caching.
"""

from __future__ import annotations

import hashlib
import time
from functools import cache
from typing import TYPE_CHECKING, Any

import numpy as np

from core.observability.logging import get_logger
from core.observability.metrics import (
    GEN_AI_OPERATION_DURATION,
    GEN_AI_TOKEN_USAGE,
)

if TYPE_CHECKING:
    from sentence_transformers import (  # type: ignore[import-untyped]
        CrossEncoder,
        SentenceTransformer,
    )
else:  # pragma: no cover - exercised by import guards
    # Runtime guarded import: mypy only ever sees the typed branch above, so
    # the None fallback never reads as "assigning to a type".
    try:
        from sentence_transformers import CrossEncoder, SentenceTransformer
    except ImportError:
        CrossEncoder = None
        SentenceTransformer = None

from core.cache import RedisTTLCache, TTLCache, create_redis_client
from core.cache.single_flight import LayeredSingleFlight, build_single_flight
from core.config import get_chat_config, get_storage_config, get_vectorstore_config
from core.utils.concurrency import run_inference

logger = get_logger(__name__)

#: OTel Gen AI ``gen_ai.operation.name`` for an embedding call. Embeddings are
#: a first-class Gen AI operation in the semantic conventions, and a retrieval
#: trace without them is missing the inference that produced the query vector.
EMBEDDING_OPERATION = "embeddings"

#: ``gen_ai.system`` for the local sentence-transformers runtime.
EMBEDDING_SYSTEM = "sentence_transformers"


def _model_name(model: Any) -> str:
    """Best-effort model identifier for span/metric labels."""
    card = getattr(model, "model_card_data", None)
    for candidate in (
        getattr(card, "base_model", None),
        getattr(card, "model_name", None),
        getattr(model, "model_name", None),
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    return type(model).__name__


def _token_usage_enabled() -> bool:
    """Whether embedding spans should carry ``gen_ai.usage.input_tokens``."""
    try:
        return bool(get_vectorstore_config().embedding_token_usage_enabled)
    except Exception as exc:  # config must never break an embedding
        # `exc` is a config-lookup failure, not a credential; the rule matches
        # on "token" (NLP tokenization here, not an auth token).
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.debug("[embedder] token-usage setting unavailable: %s", exc)
        return False


def _count_tokens_sync(model: Any, texts: list[str]) -> int | None:
    """Blocking token count for *texts*, or ``None`` if unavailable."""
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None or not texts:
        return None
    try:
        encoded = tokenizer(texts)
        ids = encoded["input_ids"] if isinstance(encoded, dict) else encoded.input_ids
        return sum(len(row) for row in ids)
    except Exception as exc:  # telemetry must never break an embedding
        # `exc` is a tokenizer failure, not a credential; the rule matches on
        # "token" (NLP tokenization here, not an auth token).
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure
        logger.debug("[embedder] token count unavailable: %s", exc)
        return None


async def _count_input_tokens(model: Any, texts: list[str]) -> int | None:
    """Token count for *texts*, or ``None`` when it is off or unavailable.

    A real ``gen_ai.usage.input_tokens`` is the only way to compare the cost of
    an embedding with the rest of the Gen AI calls, but obtaining it means
    tokenizing the text a second time — a full, CPU-bound pass. So it is
    gated on ``VECTORSTORE_EMBEDDING_TOKEN_USAGE_ENABLED`` (off by default),
    counts only the texts that actually reached the model (a cache hit pays
    nothing), and runs on the shared inference pool so it never blocks the
    event loop.
    """
    if not texts or not _token_usage_enabled():
        return None
    return await run_inference(lambda: _count_tokens_sync(model, texts))


def _record_embedding_metrics(
    model_name: str, tokens: int | None, elapsed: float
) -> None:
    """Emit the Gen AI semconv metrics for one embedding call."""
    try:
        GEN_AI_OPERATION_DURATION.labels(
            EMBEDDING_SYSTEM, model_name, EMBEDDING_OPERATION
        ).observe(elapsed)
        if tokens:
            GEN_AI_TOKEN_USAGE.labels(EMBEDDING_SYSTEM, model_name, "input").observe(
                tokens
            )
    except Exception:  # silent-ok: metrics must not fail an embedding
        pass


def _require_sentence_transformers() -> None:
    """Ensure sentence-transformers is available before using RAG models."""
    if SentenceTransformer is None or CrossEncoder is None:
        raise RuntimeError(
            "sentence-transformers is not installed. "
            "Install the optional extra with: pip install 'baselith-core[rag]'"
        )


def _supports_batch_get(cache: Any) -> bool:
    return callable(getattr(type(cache), "get_many", None))


def _supports_batch_set(cache: Any) -> bool:
    return callable(getattr(type(cache), "set_many", None))


class CachedEmbedder:
    """
    Wrapper around SentenceTransformer with caching capabilities.

    Caches embeddings to reduce computation for repeated texts.

    Example:
        ```python
        embedder = get_embedder()
        embedding = embedder.encode("Hello world")
        ```
    """

    def __init__(
        self,
        model: SentenceTransformer,
        cache: TTLCache | RedisTTLCache | None = None,
        cache_backend: str = "memory",
        redis_url: str | None = None,
        redis_prefix: str = "cache",
        cache_ttl: int = 3600,
    ):
        """
        Initialize cached embedder.

        Args:
            model: SentenceTransformer model instance
            cache: Optional external cache instance
            cache_backend: Cache backend type ("redis" or "memory")
            redis_url: Redis connection URL (required if backend is redis)
            redis_prefix: Prefix for redis keys
            cache_ttl: Cache TTL in seconds
        """
        self.model = model
        self._cache = cache

        if self._cache is None:
            try:
                if cache_backend == "redis" and redis_url:
                    redis_client = create_redis_client(redis_url)
                    self._cache = RedisTTLCache(
                        redis_client,
                        prefix=f"{redis_prefix}:embed:{model.get_sentence_embedding_dimension()}",
                        default_ttl=cache_ttl,
                    )
                else:
                    self._cache = TTLCache(maxsize=10000, ttl=cache_ttl)
            except Exception as e:
                logger.warning(f"[embedder] Failed to initialize cache: {e}")

        # Coalesces concurrent misses for the same single text (keyed by the
        # same sha256 the cache uses) so a popular query is encoded once
        # instead of once per concurrent caller.
        #
        # The cross-worker layer is offered only when the resolved cache is a
        # RedisTTLCache — i.e. genuinely shared between pods, so a worker that
        # loses the lock can read the winner's embedding back out of it. Note
        # this is decided on the *resolved* cache, not on `cache_backend`: if
        # the Redis client above failed to build we fell back to a local
        # TTLCache, and a distributed lock over a process-local store would
        # only add latency before recomputing anyway.
        resolved = self._cache
        shared = isinstance(resolved, RedisTTLCache)
        self._single_flight: LayeredSingleFlight[Any] = build_single_flight(
            shared_cache=shared,
            key_prefix=(
                f"{resolved.namespace}:sf"
                if isinstance(resolved, RedisTTLCache)
                else "baselithcore:singleflight:embed"
            ),
        )

    async def encode(
        self, sentences: str | list[str], **kwargs: Any
    ) -> list[float] | np.ndarray | list[np.ndarray]:
        """
        Encode sentences to embeddings with caching (async).

        Wrapped in an OTel Gen AI span (``gen_ai.operation.name=embeddings``)
        so the inference that produces a query vector shows up in the same
        trace as the retrieval and the completion it feeds. With
        ``VECTORSTORE_EMBEDDING_TOKEN_USAGE_ENABLED`` the span also carries
        ``gen_ai.usage.input_tokens`` for the texts that actually reached the
        model — cache hits cost nothing and are reported as such.

        Args:
            sentences: Text or list of texts to encode
            **kwargs: Additional arguments for SentenceTransformer.encode

        Returns:
            Embedding(s) as numpy array(s)
        """
        from core.observability import get_tracer

        model_name = _model_name(self.model)
        inputs = 1 if isinstance(sentences, str) else len(list(sentences))
        encoded_texts: list[str] = []
        started = time.perf_counter()
        with get_tracer("embedding-service").start_span(
            f"{EMBEDDING_OPERATION} {model_name}",
            attributes={
                "gen_ai.operation.name": EMBEDDING_OPERATION,
                "gen_ai.system": EMBEDDING_SYSTEM,
                "gen_ai.request.model": model_name,
                "gen_ai.baselith.input_count": inputs,
            },
        ) as span:
            try:
                return await self._encode(sentences, encoded_texts, **kwargs)
            finally:
                elapsed = time.perf_counter() - started
                tokens = await _count_input_tokens(self.model, encoded_texts)
                span.set_attribute("gen_ai.baselith.encoded_count", len(encoded_texts))
                span.set_attribute(
                    "gen_ai.baselith.cache_hits", max(inputs - len(encoded_texts), 0)
                )
                if tokens is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", tokens)
                _record_embedding_metrics(model_name, tokens, elapsed)

    async def _encode(
        self, sentences: str | list[str], encoded_texts: list[str], **kwargs: Any
    ) -> list[float] | np.ndarray | list[np.ndarray]:
        """Cache lookup + model inference for :meth:`encode`.

        Args:
            sentences: Text or list of texts to encode.
            encoded_texts: Out-parameter; every text actually sent to the model
                is appended, so the caller can attribute token usage to the
                real inference rather than to the cache hits.
            **kwargs: Passed through to ``SentenceTransformer.encode``.

        Returns:
            Embedding(s) as numpy array(s).
        """
        # Passthrough if cache disabled
        if not self._cache:
            encoded_texts.extend(
                [sentences] if isinstance(sentences, str) else list(sentences)
            )
            # Blocking encode offloaded to the dedicated inference pool.
            return await run_inference(lambda: self.model.encode(sentences, **kwargs))

        is_single = isinstance(sentences, str)
        inputs: list[str] = [sentences] if is_single else list(sentences)  # type: ignore[list-item]

        # 1. Identify hashes
        hashes: list[str] = [
            hashlib.sha256(text.encode("utf-8")).hexdigest() for text in inputs
        ]

        # 2. Check cache
        results: list[Any] = [None] * len(inputs)
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        if _supports_batch_get(self._cache):
            cached_values = await self._cache.get_many(hashes)
            for idx, cached_val in enumerate(cached_values):
                if cached_val is not None:
                    results[idx] = cached_val
                else:
                    missing_indices.append(idx)
                    missing_texts.append(inputs[idx])
        else:
            for idx, h in enumerate(hashes):
                cached_val = await self._cache.get(h)
                if cached_val is not None:
                    results[idx] = cached_val
                else:
                    missing_indices.append(idx)
                    missing_texts.append(inputs[idx])

        # 3. Compute missing
        if len(missing_texts) == 1:
            # Single-text miss (the stampede-prone shape: many concurrent
            # requests embedding the same query). Coalesce via single-flight
            # so only the first caller runs the model; the batch path below
            # is left alone to preserve model-level batching.
            real_idx = missing_indices[0]

            async def _encode_and_fill() -> Any:
                encoded_texts.extend(missing_texts)
                emb = (
                    await run_inference(
                        lambda: self.model.encode(missing_texts, **kwargs)
                    )
                )[0]
                await self._store_embeddings([(hashes[real_idx], emb)])
                return emb

            async def _recheck_shared() -> Any:
                # How a worker that lost the cross-worker lock obtains the
                # winner's result: the winner published it to this same shared
                # cache in _encode_and_fill. Ignored on the in-process path.
                assert self._cache is not None
                return await self._cache.get(hashes[real_idx])

            results[real_idx] = await self._single_flight.do(
                hashes[real_idx], _encode_and_fill, recheck=_recheck_shared
            )
        elif missing_texts:
            encoded_texts.extend(missing_texts)
            # Blocking model call on the dedicated inference pool.
            embeddings = await run_inference(
                lambda: self.model.encode(missing_texts, **kwargs)
            )

            # 4. Update cache
            cache_updates: list[tuple[str, Any]] = []
            for i, emb in enumerate(embeddings):
                real_idx = missing_indices[i]
                results[real_idx] = emb
                cache_updates.append((hashes[real_idx], emb))

            await self._store_embeddings(cache_updates)

        # 5. Format Output
        final_results: Any = results

        if kwargs.get("convert_to_numpy", True):
            final_results = np.array(results)

        if is_single:
            return final_results[0]  # type: ignore[return-value]

        return final_results  # type: ignore[return-value]

    async def _store_embeddings(self, cache_updates: list[tuple[str, Any]]) -> None:
        """Write computed embeddings to the cache (batch API when available)."""
        if not cache_updates or self._cache is None:
            return
        if _supports_batch_set(self._cache):
            await self._cache.set_many(cache_updates)
        else:
            for key, value in cache_updates:
                await self._cache.set(key, value)

    def __getattr__(self, name: str) -> Any:
        """Delegate other calls to model."""
        return getattr(self.model, name)


@cache
def get_embedder(model_name: str | None = None) -> CachedEmbedder:
    """
    Get cached embedder instance.

    Args:
        model_name: Name of the SentenceTransformer model (optional, defaults to config)

    Returns:
        CachedEmbedder instance
    """
    vs_config = get_vectorstore_config()
    storage_config = get_storage_config()

    _require_sentence_transformers()

    actual_model_name = model_name or vs_config.embedding_model
    assert SentenceTransformer is not None
    base_model = SentenceTransformer(actual_model_name)

    return CachedEmbedder(
        base_model,
        cache_backend=storage_config.cache_backend,
        redis_url=storage_config.cache_redis_url,
        redis_prefix=storage_config.cache_redis_prefix,
        cache_ttl=vs_config.embedding_cache_ttl,
    )


@cache
def get_reranker(model_name: str | None = None) -> CrossEncoder:
    """
    Get cached reranker instance.

    Args:
        model_name: Name of the CrossEncoder model (optional, defaults to config)

    Returns:
        CrossEncoder instance
    """
    chat_config = get_chat_config()
    _require_sentence_transformers()
    actual_model_name = model_name or chat_config.reranker_model
    assert CrossEncoder is not None
    return CrossEncoder(actual_model_name)


__all__ = [
    "EMBEDDING_OPERATION",
    "EMBEDDING_SYSTEM",
    "CachedEmbedder",
    "get_embedder",
    "get_reranker",
]
