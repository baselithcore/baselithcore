"""Pre-retrieval answer cache keys.

The response cache (``core.chat.mixins.retrieval_context.check_cache``) is
consulted *after* retrieval because its key embeds the retrieved context. That
makes it perfectly self-invalidating — a reindexed corpus yields a different
context hash, so a stale answer can never be served — but it also means a hit
has already paid for the vector search, the cross-encoder rerank and the
context build. Only LLM generation is saved.

This module builds the key for a **second, cheaper cache layer** probed
immediately after ``load_history``, keyed on ``(normalized_query,
sha256(scope + history_text))`` — no context. A hit there skips the whole
retrieval pipeline.

Freshness trade-off
-------------------
Dropping the context from the key drops the corpus-change signal with it: a
reindexed document changes neither the query nor the history. Three
mitigations, in order of strength:

1. **Corpus version in the key.** ``IndexingService.index_version`` is a
   monotonic counter bumped on every registry mutation (batch flush, stale
   deletion, state reload). Folding it into the hash means an in-process
   reindex orphans every pre-check entry at once — real invalidation, not
   just expiry. When the version cannot be read the key is withheld entirely
   (:func:`build_precheck_key` returns ``None``) and the pre-check degrades
   to a no-op rather than risking a stale hit.
2. **A separate, short TTL** (``CHAT_RAG_PRECHECK_TTL``, default 60s vs the
   response cache's 3600s). This is the only defense against a corpus mutated
   by *another* process, whose ``index_version`` this process never sees.
3. **A separate cache namespace** (Redis prefix ``…:rag_precheck``, or a
   distinct in-process ``TTLCache``), so the layer can be flushed without
   touching the response cache.

The feature is therefore **off by default** (``CHAT_RAG_PRECHECK_ENABLED``):
enabling it is an explicit choice of latency over freshness.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from core.observability.logging import get_logger
from core.services.indexing import get_indexing_service

if TYPE_CHECKING:
    from core.chat.agent_state import AgentState

logger = get_logger(__name__)

#: Key-space marker mixed into the hashed payload. Guarantees a pre-check key
#: can never collide with a response-cache key even if the two layers were
#: ever pointed at one backend, and lets the scheme be revised without
#: serving entries written under the old one.
PRECHECK_KEY_NAMESPACE = "rag_precheck:v1"

#: Cache namespace (Redis key prefix segment) for the pre-check layer.
PRECHECK_CACHE_NAMESPACE = "rag_precheck"


def get_corpus_version() -> str | None:
    """Return the current corpus invalidation token, or ``None`` if unknown.

    Wraps ``IndexingService.index_version``. A ``None`` return must disable
    the pre-check for that request: without the token the key has no way at
    all to notice a reindex.
    """
    try:
        return str(get_indexing_service().index_version)
    except Exception:  # pragma: no cover - defensive, indexing is optional
        logger.debug("precheck_corpus_version_unavailable", exc_info=True)
        return None


def build_precheck_key(state: AgentState) -> tuple[str, str] | None:
    """Build the pre-retrieval cache key for ``state``.

    The key is ``(normalized_query, sha256(scope + history_text))``. The scope
    carries the corpus version plus every dimension that the response cache
    only gets to encode *implicitly* through the retrieved context — tenant,
    knowledge-base label and the ``rag_only`` flag. Omitting them here would
    let one tenant's answer be served to another, because without a context
    hash nothing else in the key is tenant-dependent.

    Args:
        state: Current agent state, after ``load_history`` has populated
            ``normalized_query`` and ``history_text``.

    Returns:
        The cache key, or ``None`` when no key may safely be formed.
    """
    if not state.normalized_query:
        return None

    corpus_version = get_corpus_version()
    if corpus_version is None:
        return None

    request = state.request
    scope = "|".join(
        (
            PRECHECK_KEY_NAMESPACE,
            f"corpus={corpus_version}",
            f"tenant={getattr(request, 'tenant_id', None) or ''}",
            f"kb={getattr(request, 'kb_label', None) or ''}",
            f"rag_only={'1' if state.rag_only else '0'}",
        )
    )
    payload = f"{scope}\n\n====\n\n{state.history_text}"
    return (state.normalized_query, hashlib.sha256(payload.encode("utf-8")).hexdigest())


__all__ = [
    "PRECHECK_CACHE_NAMESPACE",
    "PRECHECK_KEY_NAMESPACE",
    "build_precheck_key",
    "get_corpus_version",
]
