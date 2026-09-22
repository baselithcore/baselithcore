"""The search cache must key on everything that changes the result.

Three inputs used to be missing from the key, so a hit could serve another
query's rows:

* the query vector was hashed from its **first ten components only**, so two
  384-dimension embeddings that agree on their head collided;
* ``query_filter`` (and every other provider kwarg) was absent, so the same
  vector with and without a ``document_id`` restriction shared one entry;
* ``query_text`` was absent although it drives the re-ranking stage, so two
  different questions reusing one embedding shared a re-ranked entry.

Each test pins one of those, by asserting the *rows* a second search returns —
not the key string — so the guarantee survives a change of key format.
"""

from typing import Any

import pytest

from core.services.vectorstore.orchestrator import SearchOrchestrator

pytestmark = pytest.mark.unit

DIMENSIONS = 384


class _Config:
    """Minimal stand-in for the vector-store settings object."""

    collection_name = "docs"
    search_limit = 5
    search_cache_enabled = True
    search_cache_ttl = 300


class _Hit:
    """Provider hit shaped like the Qdrant/pgvector rows the mapper reads."""

    def __init__(self, doc_id: str, score: float = 0.9) -> None:
        self.id = doc_id
        self.score = score
        self.payload = {"document_id": doc_id, "text": f"body of {doc_id}"}
        self.vector = None


class _Provider:
    """Returns a hit named after the arguments it was called with.

    That makes a wrongly-shared cache entry visible as the *previous* call's
    document id coming back for the current call.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def search(
        self,
        collection_name: str,
        query_vector: Any,
        limit: int,
        **kwargs: Any,
    ) -> list[_Hit]:
        self.calls += 1
        tail = float(list(query_vector)[-1])
        doc_filter = kwargs.get("query_filter")
        return [_Hit(f"tail={tail}|filter={doc_filter}")]


class _Cache:
    """In-memory stand-in for the async search cache."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.store[key] = value


def _vector(last: float) -> list[float]:
    """A full-width embedding whose first ten components are always equal."""
    return [0.5] * (DIMENSIONS - 1) + [last]


def _ids(results: Any) -> list[str]:
    return [r.document.id for r in results]


async def test_vectors_differing_past_the_tenth_component_do_not_collide() -> None:
    """Hashing ten of 384 components made distinct embeddings share an entry."""
    provider = _Provider()
    orchestrator = SearchOrchestrator(_Config(), provider, _Cache())

    first = await orchestrator.search(query_vector=_vector(0.1))
    second = await orchestrator.search(query_vector=_vector(0.9))

    assert _ids(first) == ["tail=0.1|filter=None"]
    assert _ids(second) == ["tail=0.9|filter=None"]
    assert provider.calls == 2, "second vector was served from the first one's entry"


async def test_query_filter_is_part_of_the_key() -> None:
    """The same vector filtered and unfiltered must not share an entry."""
    provider = _Provider()
    orchestrator = SearchOrchestrator(_Config(), provider, _Cache())

    unfiltered = await orchestrator.search(query_vector=_vector(0.1))
    filtered = await orchestrator.search(
        query_vector=_vector(0.1), query_filter={"document_id": "report-7"}
    )

    assert _ids(unfiltered) == ["tail=0.1|filter=None"]
    assert _ids(filtered) == ["tail=0.1|filter={'document_id': 'report-7'}"]
    assert provider.calls == 2, "the filtered search was served the unfiltered rows"


async def test_repeating_one_search_still_hits_the_cache() -> None:
    """The key must stay stable across identical calls, or caching is pointless."""
    provider = _Provider()
    orchestrator = SearchOrchestrator(_Config(), provider, _Cache())

    first = await orchestrator.search(query_vector=_vector(0.1))
    second = await orchestrator.search(query_vector=_vector(0.1))

    assert _ids(first) == _ids(second)
    assert provider.calls == 1, "an identical repeat missed the cache"


async def test_query_text_is_part_of_the_key_when_reranking() -> None:
    """Re-ranking is driven by ``query_text``; two questions must not share one entry."""
    provider = _Provider()
    orchestrator = SearchOrchestrator(_Config(), provider, _Cache())

    await orchestrator.search(
        query_vector=_vector(0.1), query_text="what is the revenue", rerank=True
    )
    await orchestrator.search(
        query_vector=_vector(0.1), query_text="who signed the contract", rerank=True
    )

    assert provider.calls == 2, "a different question reused the first one's ranking"


async def test_an_unserializable_kwarg_disables_the_cache_rather_than_colliding() -> (
    None
):
    """A filter the key cannot represent must force a miss, never a blind hit."""

    class _Opaque:
        __slots__ = ()

    provider = _Provider()
    orchestrator = SearchOrchestrator(_Config(), provider, _Cache())

    await orchestrator.search(query_vector=_vector(0.1), query_filter=_Opaque())
    await orchestrator.search(query_vector=_vector(0.1), query_filter=_Opaque())

    assert provider.calls == 2, "two opaque filters were treated as one cache entry"
