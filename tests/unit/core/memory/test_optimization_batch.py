"""delete_items must use the provider's batch API when it has one.

Compaction used to issue one ``provider.delete`` round-trip per memory (up to
1000 sequential deletes per pass); providers exposing ``delete_many`` now get
a single batched call, mirroring ``add_items``/``add_many``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from core.memory.optimization_batch import delete_items


async def test_delete_items_prefers_delete_many():
    provider = AsyncMock()
    await delete_items(provider, ["a", "b", "c"], fanout_limit=8)
    provider.delete_many.assert_awaited_once_with(["a", "b", "c"])
    provider.delete.assert_not_awaited()


class _PerItemProvider:
    """Provider WITHOUT delete_many — only the item-at-a-time protocol."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    async def delete(self, item_id: str) -> bool:
        self.deleted.append(item_id)
        return True


async def test_delete_items_falls_back_to_per_item_deletes():
    provider = _PerItemProvider()
    await delete_items(provider, ["a", "b"], fanout_limit=8)
    assert sorted(provider.deleted) == ["a", "b"]


async def test_delete_items_noop_on_empty():
    provider = AsyncMock()
    await delete_items(provider, [], fanout_limit=8)
    provider.delete_many.assert_not_awaited()
