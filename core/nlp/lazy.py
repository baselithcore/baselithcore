"""
Deferred model loading for embedders and rerankers.

``get_embedder`` / ``get_reranker`` build a sentence-transformers model
synchronously: seconds of disk and CPU work, and a ``RuntimeError`` when the
``[rag]`` extra is absent. Neither belongs on an import or boot path, nor on
the event loop. This module offers two ways around both:

* :func:`aget_embedder` / :func:`aget_reranker` — async accessors that run the
  cached factory in a worker thread, for code that needs the model now.
* :class:`LazyEmbedder` / :class:`LazyReranker` — stand-ins that hold a
  factory and a model name and build the model only on first real use, so a
  dependency container can be constructed on a plain install without loading
  (or even being able to load) any model.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Callable
from typing import Any

from core.nlp import models as _models
from core.nlp.models import CachedEmbedder
from core.utils.concurrency import run_inference

ModelFactory = Callable[[Any], Any]

# Serializes first loads: ``functools.cache`` does not lock, so two threads
# racing through a cold ``get_embedder`` would each build the model.
_LOAD_LOCK = threading.Lock()


def _load_embedder(model_name: str | None) -> CachedEmbedder:
    # Resolved through the module at call time so tests can patch the factory.
    with _LOAD_LOCK:
        return _models.get_embedder(model_name)


def _load_reranker(model_name: str | None) -> Any:
    with _LOAD_LOCK:
        return _models.get_reranker(model_name)


async def aget_embedder(model_name: str | None = None) -> CachedEmbedder:
    """Return the cached embedder, loading it off the event loop.

    Args:
        model_name: SentenceTransformer model id; ``None`` uses the configured
            ``VECTORSTORE_EMBEDDING_MODEL``.

    Returns:
        The process-wide :class:`CachedEmbedder` for ``model_name``.

    Raises:
        RuntimeError: If sentence-transformers is not installed.
    """
    return await asyncio.to_thread(_load_embedder, model_name)


async def aget_reranker(model_name: str | None = None) -> Any:
    """Return the cached cross-encoder, loading it off the event loop.

    Args:
        model_name: CrossEncoder model id; ``None`` uses the configured
            ``CHAT_RERANKER_MODEL``.

    Returns:
        The process-wide CrossEncoder for ``model_name``.

    Raises:
        RuntimeError: If sentence-transformers is not installed.
    """
    return await asyncio.to_thread(_load_reranker, model_name)


class _LazyModel:
    """Holds a factory and builds its model exactly once, on first use."""

    def __init__(self, factory: ModelFactory, model_name: str | None) -> None:
        self._factory = factory
        self._model_name = model_name
        self._instance: Any = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        """Whether the underlying model has been built."""
        return self._instance is not None

    @property
    def model_name(self) -> str | None:
        """The model id the factory will be (or was) called with."""
        return self._model_name

    def load(self) -> Any:
        """Build (once) and return the model. Blocking — call off the loop."""
        if self._instance is None:
            with self._lock:
                if self._instance is None:
                    self._instance = self._factory(self._model_name)
        return self._instance

    async def aload(self) -> Any:
        """Build (once) and return the model from a worker thread."""
        if self._instance is not None:
            return self._instance
        return await asyncio.to_thread(self.load)

    def __getattr__(self, name: str) -> Any:
        # Private/dunder lookups must not trigger a model load (copy, pickle,
        # mock introspection all probe them).
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.load(), name)


class LazyEmbedder(_LazyModel):
    """Embedder stand-in whose model is built on the first ``encode`` call.

    ``encode`` is always a coroutine, matching :class:`CachedEmbedder`: the
    model is loaded in a worker thread, then an async ``encode`` is awaited
    and a sync one is offloaded to the inference pool.
    """

    async def encode(self, *args: Any, **kwargs: Any) -> Any:
        """Encode text(s) with the underlying model, loading it first if needed.

        Args:
            *args: Positional arguments forwarded to the model's ``encode``.
            **kwargs: Keyword arguments forwarded to the model's ``encode``.

        Returns:
            Whatever the underlying model's ``encode`` returns.
        """
        model = await self.aload()
        if inspect.iscoroutinefunction(model.encode):
            return await model.encode(*args, **kwargs)
        return await run_inference(lambda: model.encode(*args, **kwargs))


class LazyReranker(_LazyModel):
    """Cross-encoder stand-in whose model is built on the first ``predict``.

    ``predict`` is synchronous (as on ``CrossEncoder``) and is invoked through
    ``run_inference`` by the reranking pipeline, so the one-time load happens
    on the inference pool, never on the event loop.
    """

    def predict(self, *args: Any, **kwargs: Any) -> Any:
        """Score pairs with the underlying model, loading it first if needed.

        Args:
            *args: Positional arguments forwarded to the model's ``predict``.
            **kwargs: Keyword arguments forwarded to the model's ``predict``.

        Returns:
            Whatever the underlying model's ``predict`` returns.
        """
        return self.load().predict(*args, **kwargs)


__all__ = [
    "LazyEmbedder",
    "LazyReranker",
    "aget_embedder",
    "aget_reranker",
]
