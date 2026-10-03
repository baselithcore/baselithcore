"""``EmbeddingService`` — async text embeddings behind one interface.

Backends:

* ``remote`` (default): a model server over HTTP — the platform's Hugging
  Face TEI, or a customer's own OpenAI-compatible endpoint (``api``; see
  :mod:`core.services.inference._protocols`), with optional private-CA /
  mutual TLS.
* ``local`` (development, explicit opt-in): sentence-transformers, loaded
  lazily as a per-process singleton.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from core.config.inference import EmbeddingConfig, get_embedding_config
from core.services.inference._http import RemoteClient, tls_context
from core.services.inference._protocols import (
    EMBED_PATHS,
    embed_payload,
    parse_embeddings,
)
from core.services.inference.errors import InferenceConfigError, InferenceError


class EmbeddingBackend(Protocol):
    """Turns one batch of texts into vectors."""

    async def embed(self, texts: list[str], *, is_query: bool) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class RemoteEmbeddingBackend:
    """HTTP client for the configured embedding protocol."""

    def __init__(
        self,
        config: EmbeddingConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.url:
            raise InferenceConfigError(
                "BASELITH_EMBEDDING_URL is required for the 'remote' embedding "
                "backend (or set BASELITH_EMBEDDING_BACKEND=local for development)."
            )
        self._http = RemoteClient(
            base_url=config.url,
            timeout=config.timeout,
            max_retries=config.max_retries,
            backoff_base=config.backoff_base,
            api_key=config.api_key.get_secret_value() if config.api_key else None,
            max_total_seconds=config.max_total_seconds,
            max_response_bytes=config.max_response_bytes,
            retry_rate_limited=config.retry_rate_limited,
            allow_insecure_key=config.allow_insecure_key,
            verify=tls_context(config.ca_bundle, config.client_cert, config.client_key),
            transport=transport,
        )
        self._api = config.api
        self._model = config.model
        self._path = config.path or EMBED_PATHS[config.api]
        self._query_prefix = config.query_prefix
        self._document_prefix = config.document_prefix

    async def embed(self, texts: list[str], *, is_query: bool) -> list[list[float]]:
        prefix = self._query_prefix if is_query else self._document_prefix
        inputs = [prefix + t for t in texts] if prefix else texts
        body = await self._http.post_json(
            self._path, embed_payload(self._api, self._model, inputs)
        )
        return parse_embeddings(self._api, body, len(texts))

    async def aclose(self) -> None:
        await self._http.aclose()


class LocalEmbeddingBackend:
    """sentence-transformers backend (dev only); runs off the event loop."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self._model_id = config.model
        self._batch = config.batch_size

    async def embed(self, texts: list[str], *, is_query: bool) -> list[list[float]]:
        from core.services.inference._local import load_embedder

        def _run() -> list[list[float]]:
            model: Any = load_embedder(self._model_id)
            # sentence-transformers >= 5 applies the model's own "query"
            # prompt in encode_query, the local twin of the query prefix.
            encode = getattr(model, "encode_query", None) if is_query else None
            vecs = (encode or model.encode)(
                texts,
                batch_size=self._batch,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return [v.tolist() for v in vecs]

        return await asyncio.to_thread(_run)

    async def aclose(self) -> None:
        return None


class EmbeddingService:
    """Async embeddings with transparent batching."""

    def __init__(
        self,
        backend: EmbeddingBackend,
        *,
        model: str,
        dim: int,
        batch_size: int,
    ) -> None:
        self._backend = backend
        self._model = model
        self._dim = dim
        self._batch_size = batch_size

    @classmethod
    def from_config(
        cls,
        config: EmbeddingConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> EmbeddingService:
        """Build the service for ``config`` (default: the env-driven config)."""
        cfg = config or get_embedding_config()
        backend: EmbeddingBackend = (
            LocalEmbeddingBackend(cfg)
            if cfg.backend == "local"
            else RemoteEmbeddingBackend(cfg, transport=transport)
        )
        return cls(backend, model=cfg.model, dim=cfg.dim, batch_size=cfg.batch_size)

    @property
    def model(self) -> str:
        """Embedding model id."""
        return self._model

    @property
    def dim(self) -> int:
        """Dimension of the produced vectors."""
        return self._dim

    def _checked(self, vectors: list[list[float]]) -> list[list[float]]:
        """Refuse vectors of the wrong size before they reach a collection.

        A customer's own model rarely has bge-m3's 1024 dimensions; written
        into a collection created for ``dim`` they would fail on upsert, or —
        worse — a collection would be created at the wrong size. Fail on the
        first call, with the fix in the message.
        """
        for vec in vectors:
            if len(vec) != self._dim:
                raise InferenceError(
                    f"embedding model '{self._model}' returned {len(vec)}-dim "
                    f"vectors, configured dim is {self._dim}: set "
                    "BASELITH_EMBEDDING_DIM to the model's size (and re-index)."
                )
        return vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts`` (batched); output order matches input order.

        ``BASELITH_EMBEDDING_DOCUMENT_PREFIX`` applies; use :meth:`embed_query`
        / :meth:`embed_queries` for the search side.
        """
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            out.extend(self._checked(await self._backend.embed(batch, is_query=False)))
        return out

    async def embed_query(self, text: str) -> list[float]:
        """Embed one query string (``BASELITH_EMBEDDING_QUERY_PREFIX`` applies)."""
        return self._checked(await self._backend.embed([text], is_query=True))[0]

    async def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """Embed several queries (batched; the query prefix applies to each)."""
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            out.extend(self._checked(await self._backend.embed(batch, is_query=True)))
        return out

    async def shutdown(self) -> None:
        """Close the backend (called by the lazy registry at shutdown)."""
        await self._backend.aclose()
