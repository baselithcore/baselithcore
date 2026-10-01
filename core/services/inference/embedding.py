"""``EmbeddingService`` — async text embeddings behind one interface.

Backends:

* ``remote`` (default): Hugging Face Text Embeddings Inference,
  ``POST /embed`` with ``{"inputs": [...]}``.
* ``local`` (development, explicit opt-in): sentence-transformers, loaded
  lazily as a per-process singleton.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from core.config.inference import EmbeddingConfig, get_embedding_config
from core.services.inference._http import RemoteClient
from core.services.inference.errors import InferenceConfigError, InferenceError


class EmbeddingBackend(Protocol):
    """Turns one batch of texts into vectors."""

    async def embed(self, texts: list[str], *, is_query: bool) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


class RemoteEmbeddingBackend:
    """TEI ``/embed`` client."""

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
            transport=transport,
        )

    async def embed(self, texts: list[str], *, is_query: bool) -> list[list[float]]:
        body = await self._http.post_json(
            "/embed", {"inputs": texts, "normalize": True, "truncate": True}
        )
        if not isinstance(body, list) or len(body) != len(texts):
            raise InferenceError(
                f"/embed returned {len(body) if isinstance(body, list) else 'a non-list'} "
                f"vectors for {len(texts)} inputs"
            )
        return [[float(x) for x in vec] for vec in body]

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
            vecs = model.encode(
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

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed ``texts`` (batched); output order matches input order."""
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            out.extend(await self._backend.embed(batch, is_query=False))
        return out

    async def embed_query(self, text: str) -> list[float]:
        """Embed one query string."""
        return (await self._backend.embed([text], is_query=True))[0]

    async def shutdown(self) -> None:
        """Close the backend (called by the lazy registry at shutdown)."""
        await self._backend.aclose()
