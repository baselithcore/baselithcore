"""``RerankService`` — async cross-encoder reranking.

``remote`` uses TEI ``POST /rerank``; ``local`` (development) loads a
CrossEncoder lazily as a per-process singleton.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol

import httpx

from core.config.inference import RerankConfig, get_rerank_config
from core.services.inference._http import RemoteClient
from core.services.inference.errors import InferenceConfigError, InferenceError


class RerankBackend(Protocol):
    """Scores one batch of passages against a query."""

    async def score(self, query: str, texts: list[str]) -> list[float]: ...

    async def aclose(self) -> None: ...


class RemoteRerankBackend:
    """TEI ``/rerank`` client; returns scores in input order."""

    def __init__(
        self,
        config: RerankConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.url:
            raise InferenceConfigError(
                "BASELITH_RERANK_URL is required for the 'remote' rerank backend "
                "(or set BASELITH_RERANK_BACKEND=local for development)."
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
            transport=transport,
        )

    async def score(self, query: str, texts: list[str]) -> list[float]:
        body = await self._http.post_json(
            "/rerank",
            {"query": query, "texts": texts, "raw_scores": False, "truncate": True},
        )
        if not isinstance(body, list):
            raise InferenceError("/rerank returned a non-list body")
        scores = [0.0] * len(texts)
        seen = 0
        for row in body:
            idx = int(row["index"])
            if not 0 <= idx < len(texts):
                raise InferenceError(f"/rerank returned out-of-range index {idx}")
            scores[idx] = float(row["score"])
            seen += 1
        if seen != len(texts):
            raise InferenceError(f"/rerank scored {seen} of {len(texts)} texts")
        return scores

    async def aclose(self) -> None:
        await self._http.aclose()


class LocalRerankBackend:
    """CrossEncoder backend (dev only); runs off the event loop."""

    def __init__(self, config: RerankConfig) -> None:
        self._model_id = config.model

    async def score(self, query: str, texts: list[str]) -> list[float]:
        from core.services.inference._local import load_reranker

        def _run() -> list[float]:
            model: Any = load_reranker(self._model_id)
            raw = model.predict([(query, t) for t in texts], show_progress_bar=False)
            return [float(s) for s in raw]

        return await asyncio.to_thread(_run)

    async def aclose(self) -> None:
        return None


class RerankService:
    """Rerank passages; candidates beyond ``max_candidates`` are dropped."""

    def __init__(
        self,
        backend: RerankBackend,
        *,
        model: str,
        max_candidates: int,
        batch_size: int,
    ) -> None:
        self._backend = backend
        self._model = model
        self._max = max_candidates
        self._batch = batch_size

    @classmethod
    def from_config(
        cls,
        config: RerankConfig | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> RerankService:
        """Build the service for ``config`` (default: the env-driven config)."""
        cfg = config or get_rerank_config()
        backend: RerankBackend = (
            LocalRerankBackend(cfg)
            if cfg.backend == "local"
            else RemoteRerankBackend(cfg, transport=transport)
        )
        return cls(
            backend,
            model=cfg.model,
            max_candidates=cfg.max_candidates,
            batch_size=cfg.batch_size,
        )

    @property
    def model(self) -> str:
        """Reranker model id."""
        return self._model

    async def rerank(
        self, query: str, texts: list[str], top_k: int
    ) -> list[tuple[int, float]]:
        """Return up to ``top_k`` ``(index, score)`` pairs, best first.

        ``index`` refers to ``texts``. Only the first ``max_candidates`` texts
        are scored.
        """
        candidates = texts[: self._max]
        scores: list[float] = []
        for start in range(0, len(candidates), self._batch):
            scores.extend(
                await self._backend.score(
                    query, candidates[start : start + self._batch]
                )
            )
        ranked = sorted(enumerate(scores), key=lambda p: p[1], reverse=True)
        return ranked[: max(0, top_k)]

    async def shutdown(self) -> None:
        """Close the backend (called by the lazy registry at shutdown)."""
        await self._backend.aclose()
