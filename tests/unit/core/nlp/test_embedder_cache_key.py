"""The embedder's cache key must name the model, not just the text.

``CachedEmbedder`` hashed ``sha256(text)`` alone and disambiguated models only
through the Redis key *prefix*, which carries the embedding dimension. Two
different models of the same width — the common case, 384 — therefore shared
every entry, so a query embedded by one model could be answered with the
other's vector, silently wrecking the geometry of every similarity score.

The sibling implementation in ``core/services/vectorstore/embedding_cache.py``
already keys on ``f"{model_id}:{text}"``; these tests pin the same guarantee
here so the two cannot drift apart again.
"""

from typing import Any

import pytest

from core.nlp.models import CachedEmbedder

pytestmark = pytest.mark.unit


class _ModelCard:
    def __init__(self, name: str) -> None:
        self.base_model = name
        self.model_name = name


class _Model:
    """Stand-in for a SentenceTransformer with a recognizable output."""

    def __init__(self, name: str, marker: float) -> None:
        self.model_card_data = _ModelCard(name)
        self.model_name = name
        self._marker = marker
        self.encoded: list[str] = []

    def get_sentence_embedding_dimension(self) -> int:
        return 384

    def encode(self, sentences: Any, **_: Any) -> list[list[float]]:
        texts = [sentences] if isinstance(sentences, str) else list(sentences)
        self.encoded.extend(texts)
        return [[self._marker] * 384 for _ in texts]


class _Cache:
    """In-memory stand-in shared between two embedders, as Redis would be."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.store[key] = value


async def test_two_models_of_equal_width_do_not_share_cache_entries() -> None:
    """A shared cache must not let one model answer for another."""
    cache = _Cache()
    first_model = _Model("all-MiniLM-L6-v2", marker=1.0)
    second_model = _Model("paraphrase-MiniLM-L6-v2", marker=2.0)

    first = CachedEmbedder(first_model, cache=cache)  # type: ignore[arg-type]
    second = CachedEmbedder(second_model, cache=cache)  # type: ignore[arg-type]

    await first.encode("the same sentence")
    second_vector = await second.encode("the same sentence")

    assert second_model.encoded == ["the same sentence"], (
        "the second model was served the first model's vector"
    )
    assert second_vector[0] == 2.0


async def test_one_model_still_reuses_its_own_entries() -> None:
    """Scoping the key by model must not defeat caching within a model."""
    cache = _Cache()
    model = _Model("all-MiniLM-L6-v2", marker=1.0)
    embedder = CachedEmbedder(model, cache=cache)  # type: ignore[arg-type]

    await embedder.encode("repeated sentence")
    await embedder.encode("repeated sentence")

    assert model.encoded == ["repeated sentence"], "a repeat re-ran the model"


async def test_batch_path_is_scoped_by_model_too() -> None:
    """The multi-text branch keys entries the same way as the single-text one."""
    cache = _Cache()
    first_model = _Model("all-MiniLM-L6-v2", marker=1.0)
    second_model = _Model("paraphrase-MiniLM-L6-v2", marker=2.0)

    first = CachedEmbedder(first_model, cache=cache)  # type: ignore[arg-type]
    second = CachedEmbedder(second_model, cache=cache)  # type: ignore[arg-type]

    await first.encode(["alpha", "beta"])
    await second.encode(["alpha", "beta"])

    assert second_model.encoded == ["alpha", "beta"], (
        "the batch path served another model's vectors"
    )
