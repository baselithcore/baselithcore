"""Query vs document embeddings: one convention for local and remote models.

Asymmetric retrieval models (e5, bge with an instruction, Qwen3-Embedding)
embed the *search* side differently from the *indexed* side. sentence-
transformers spells it ``encode_query`` / ``encode_document`` (applying the
model's ``prompts["query"]`` / ``prompts["document"]``); the remote stand-in
in :mod:`core.nlp._remote` exposes the same pair, backed by
``BASELITH_EMBEDDING_QUERY_PREFIX`` / ``..._DOCUMENT_PREFIX``. Plain
``encode`` stays the indexing side.

Query-side call sites (retrieval, memory recall, routing) go through
:func:`aencode_query`, which falls back to ``encode`` for an embedder that
predates the convention — a custom implementation or a test double — so no
caller has to know which kind it holds.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Callable
from typing import Any

QUERY = "query"
"""Role of the search side (``encode_query``)."""

DOCUMENT = "document"
"""Role of the indexed side (``encode``)."""


def model_prompt(model: Any, role: str) -> str:
    """The text the model prepends for ``role`` (``""`` when it has none)."""
    prompts = getattr(model, "prompts", None)
    if not isinstance(prompts, dict):
        return ""
    prompt = prompts.get(role)
    return prompt if isinstance(prompt, str) else ""


def cache_key(text: str, model_id: str, role: str = DOCUMENT, prompt: str = "") -> str:
    """Cache key scoped to the text, the model, the role and its prompt.

    Keying on the text alone made two models of the same width share every
    entry — the Redis prefix only carries the embedding *dimension* — so one
    model's vector could answer for another's. The role and prompt matter for
    the same reason: a query embedded with a prefix is a different vector from
    the same text indexed as a document, and a changed prefix must not serve a
    vector cached under the old one. A prompt-less document keeps the plain
    ``model:text`` form. :mod:`core.services.vectorstore.embedding_cache`
    builds its index-time keys with this function too (role ``document``,
    the dimension folded into ``model_id``), so the two cannot drift.
    """
    if role == DOCUMENT and not prompt:
        raw = f"{model_id}:{text}"
    else:
        raw = f"{model_id}:{role}:{prompt}:{text}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _defines(obj: Any, method: str) -> bool:
    # Looked up on the type: a ``MagicMock`` answers every attribute on the
    # instance, which would route a test double's query away from the
    # ``encode`` its test configured.
    return callable(getattr(type(obj), method, None))


def model_encoder(model: Any, role: str) -> Callable[..., Any]:
    """The model method that embeds ``role`` (``encode`` when it has no twin)."""
    if role == QUERY and _defines(model, "encode_query"):
        method: Callable[..., Any] = model.encode_query
        return method
    encode: Callable[..., Any] = model.encode
    return encode


async def aencode_query(embedder: Any, sentences: Any, **kwargs: Any) -> Any:
    """Embed the search side with any embedder, async or sync, off the loop.

    Args:
        embedder: A :class:`~core.nlp.models.CachedEmbedder`, a
            :class:`~core.nlp.lazy.LazyEmbedder`, a sentence-transformers
            model or anything with an ``encode`` method.
        sentences: One query or a list of them.
        **kwargs: Passed to the embedder's method.

    Returns:
        Whatever the embedder returns (numpy for the built-in ones).
    """
    method = model_encoder(embedder, QUERY)
    if inspect.iscoroutinefunction(method):
        result = await method(sentences, **kwargs)
    else:
        result = await asyncio.to_thread(method, sentences, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


__all__ = [
    "DOCUMENT",
    "QUERY",
    "aencode_query",
    "cache_key",
    "model_encoder",
    "model_prompt",
]
