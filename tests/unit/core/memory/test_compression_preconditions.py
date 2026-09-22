"""Compaction refuses rather than degrades, and deletes only what it folded.

``compress_old_memories`` is the one destructive path in the memory layer, and
it used to run unconditionally: it read its batch from ``provider.search("")``
— the nearest neighbours of the empty string's embedding, a slice that changed
between runs — built a compressor with no ``llm_service`` so summarisation fell
back to ``" | ".join(m.content[:100] for m in memories[:3])``, then deleted
every id it had read. Up to 500 memories traded for a 300-character truncation
of three of them, with no dry run and no restore.

Each test below pins one of the three preconditions, and the last two pin what
is deleted once they hold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.memory.manager import AgentMemory
from core.memory.providers import InMemoryProvider
from core.memory.types import MemoryItem, MemoryType


def _old_item(content: str) -> MemoryItem:
    """A memory the relevance calculator puts in the *compress* bucket.

    With the default 7-day half life and an importance of 0.5, a 12-day-old
    item scores ~0.15 — under ``compression_threshold`` (0.3) and over
    ``pruning_threshold`` (0.1).
    """
    return MemoryItem(
        content=content,
        memory_type=MemoryType.EPISODIC,
        created_at=datetime.now(UTC) - timedelta(days=12),
        score=0.5,
    )


def _stale_item(content: str) -> MemoryItem:
    """A memory the calculator puts in the *prune* bucket (~0.07 at 20 days)."""
    return MemoryItem(
        content=content,
        memory_type=MemoryType.EPISODIC,
        created_at=datetime.now(UTC) - timedelta(days=20),
        score=0.5,
    )


def _summarizer(text: str = "one summary") -> Any:
    llm = MagicMock()
    llm.generate_response = AsyncMock(return_value=text)
    return llm


async def _stocked_provider(*items: MemoryItem) -> InMemoryProvider:
    provider = InMemoryProvider()
    for item in items:
        await provider.add(item)
    return provider


class TestPreconditions:
    async def test_refuses_without_a_summarizer(self) -> None:
        provider = await _stocked_provider(_old_item("a"), _old_item("b"))
        memory = AgentMemory(provider=provider)

        assert await memory.compress_old_memories() is None
        assert len(provider._checkpoints) == 2

    async def test_refuses_when_the_provider_cannot_enumerate(self) -> None:
        """A store that can only be searched cannot be safely compacted."""
        provider = MagicMock()
        provider.search = AsyncMock(return_value=[_old_item("a")])
        provider.delete = AsyncMock()
        del provider.list_items
        memory = AgentMemory(provider=provider, llm_service=_summarizer())

        assert await memory.compress_old_memories() is None
        provider.delete.assert_not_awaited()

    async def test_does_not_fall_back_to_similarity_search(self) -> None:
        provider = await _stocked_provider(_old_item("a"))
        provider.search = AsyncMock(side_effect=AssertionError("search() was called"))
        memory = AgentMemory(provider=provider, llm_service=_summarizer())

        await memory.compress_old_memories()

    async def test_empty_store_reports_a_zero_result(self) -> None:
        memory = AgentMemory(provider=InMemoryProvider(), llm_service=_summarizer())

        result = await memory.compress_old_memories()

        assert result is not None
        assert result.original_count == 0


class TestWhatGetsDeleted:
    async def test_only_the_folded_sources_are_deleted(self) -> None:
        folded = [_old_item("old one"), _old_item("old two")]
        kept = MemoryItem(content="fresh", memory_type=MemoryType.EPISODIC, score=1.0)
        provider = await _stocked_provider(*folded, kept)
        memory = AgentMemory(provider=provider, llm_service=_summarizer())

        result = await memory.compress_old_memories()

        assert result is not None
        remaining = provider._checkpoints
        assert str(kept.id) in remaining
        for item in folded:
            assert str(item.id) not in remaining
        summaries = [
            item for item in remaining.values() if item.metadata.get("is_summary")
        ]
        assert len(summaries) == 1
        assert summaries[0].content == "one summary"

    async def test_summary_is_written_before_its_sources_are_removed(self) -> None:
        """A crash between the phases leaves a duplicate, never a hole."""
        provider = await _stocked_provider(_old_item("a"), _old_item("b"))
        order: list[str] = []
        real_add, real_delete = provider.add, provider.delete

        async def add(item: MemoryItem) -> None:
            order.append("add")
            await real_add(item)

        async def delete(item_id: str) -> bool:
            order.append("delete")
            return await real_delete(item_id)

        provider.add, provider.delete = add, delete
        memory = AgentMemory(provider=provider, llm_service=_summarizer())

        await memory.compress_old_memories()

        assert order, "compaction did nothing"
        assert order.index("add") < order.index("delete")

    async def test_a_failed_summary_deletes_nothing(self) -> None:
        llm = MagicMock()
        llm.generate_response = AsyncMock(side_effect=RuntimeError("provider down"))
        provider = await _stocked_provider(_old_item("a"), _old_item("b"))
        memory = AgentMemory(provider=provider, llm_service=llm)

        await memory.compress_old_memories()

        assert len(provider._checkpoints) == 2


class TestPruningIsOptIn:
    async def test_below_threshold_memories_survive_by_default(self) -> None:
        """Compaction folds; discarding aged-out memories is a separate ask."""
        stale = _stale_item("aged out")
        provider = await _stocked_provider(stale, _old_item("foldable one"))
        memory = AgentMemory(provider=provider, llm_service=_summarizer())

        result = await memory.compress_old_memories()

        assert result is not None
        assert result.pruned_count == 1, "the candidate is still reported"
        assert str(stale.id) in provider._checkpoints

    async def test_prune_flag_deletes_them(self) -> None:
        stale = _stale_item("aged out")
        kept = MemoryItem(content="fresh", memory_type=MemoryType.EPISODIC, score=1.0)
        provider = await _stocked_provider(stale, kept)
        memory = AgentMemory(provider=provider, llm_service=_summarizer())

        await memory.compress_old_memories(prune=True)

        assert str(stale.id) not in provider._checkpoints
        assert str(kept.id) in provider._checkpoints


class TestInMemoryProviderEnumeration:
    async def test_pages_in_insertion_order_and_ends(self) -> None:
        items = [_old_item(str(n)) for n in range(5)]
        provider = await _stocked_provider(*items)

        first, offset = await provider.list_items(limit=2)
        assert [i.content for i in first] == ["0", "1"]
        assert offset == 2

        second, offset = await provider.list_items(limit=2, offset=offset)
        assert [i.content for i in second] == ["2", "3"]

        last, offset = await provider.list_items(limit=2, offset=offset)
        assert [i.content for i in last] == ["4"]
        assert offset is None


@pytest.mark.asyncio
async def test_vector_provider_preserves_the_stored_id() -> None:
    """A recalled item must be addressable, or a delete keyed on it is a no-op."""
    from unittest.mock import patch

    from core.models.domain import Document, SearchResult

    stored = MemoryItem(content="body", memory_type=MemoryType.LONG_TERM)
    hit = SearchResult(
        document=Document(
            id=str(stored.id), content="body", metadata={"type": "long_term"}
        ),
        score=0.9,
    )

    with patch("core.memory.providers.get_vectorstore_service") as get_service:
        service = MagicMock()
        service.search = AsyncMock(return_value=[hit])
        get_service.return_value = service

        from core.memory.providers import VectorMemoryProvider

        provider = VectorMemoryProvider(embedder=MagicMock())
        recalled = await provider.search("body", query_vector=[0.1, 0.2])

    assert recalled[0].id == stored.id


class TestPersistenceIsWiredEverywhere:
    """Both construction sites must agree about whether memories persist.

    The lazy-registry resource and ``core.memory.get_memory()`` were each built
    bare, so "long-term memory" was a bounded in-process deque that died with
    the worker whatever the configuration said.
    """

    def test_the_singleton_gets_a_provider_and_a_summarizer(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import core.memory as memory_module

        sentinel = InMemoryProvider()
        monkeypatch.setattr(memory_module, "_agent_memory", None)
        monkeypatch.setattr(
            "core.memory.providers.build_memory_provider", lambda _c: sentinel
        )
        monkeypatch.setattr("core.services.llm.get_llm_service", lambda: _summarizer())

        manager = memory_module.get_memory()

        assert manager.provider is sentinel
        assert manager.llm_service is not None

    def test_persistence_can_be_switched_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from core.config.memory import MemoryRuntimeConfig
        from core.memory.providers import build_memory_provider

        monkeypatch.setattr(
            "core.config.memory.get_memory_runtime_config",
            lambda: MemoryRuntimeConfig(persistence_enabled=False),
        )

        assert build_memory_provider("agent_memory") is None

    async def test_the_provider_defers_loading_its_embedder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Construction must stay cheap: a sync caller may be on the loop."""
        from unittest.mock import patch

        from core.memory.providers import VectorMemoryProvider

        with patch("core.memory.providers.get_vectorstore_service"):
            provider = VectorMemoryProvider()
        assert provider.embedder is None

        loaded = MagicMock()
        loaded.encode = AsyncMock(return_value=[[0.1]])
        with patch("core.nlp.models.get_embedder", return_value=loaded):
            assert await provider._resolve_embedder() is loaded
        assert provider.embedder is loaded
