"""Regression tests: RedisFeedbackStore loads items with a single MGET.

The load paths used to issue one GET per item id (N round trips); they now
batch all lookups into one MGET.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

from core.learning.feedback import FeedbackItem, RedisFeedbackStore


def _item_payload(item: FeedbackItem) -> bytes:
    return json.dumps(item.to_dict()).encode("utf-8")


def _items() -> list[FeedbackItem]:
    return [FeedbackItem(agent_id="a1", task_id=f"t{i}", score=0.5) for i in range(3)]


async def test_load_by_agent_uses_single_mget() -> None:
    items = _items()
    redis = AsyncMock()
    redis.smembers.return_value = [str(i.id) for i in items]
    redis.mget.return_value = [_item_payload(i) for i in items]

    store = RedisFeedbackStore(redis, key_prefix="fb:")
    loaded = await store.load_by_agent("a1")

    assert len(loaded) == 3
    assert {i.task_id for i in loaded} == {"t0", "t1", "t2"}
    redis.mget.assert_awaited_once()
    redis.get.assert_not_awaited()


async def test_load_all_uses_single_mget_and_skips_missing() -> None:
    items = _items()
    redis = AsyncMock()
    redis.lrange.return_value = [str(i.id) for i in items]
    # Middle item expired between LRANGE and MGET → None is skipped.
    redis.mget.return_value = [
        _item_payload(items[0]),
        None,
        _item_payload(items[2]),
    ]

    store = RedisFeedbackStore(redis, key_prefix="fb:")
    loaded = await store.load_all()

    assert len(loaded) == 2
    redis.mget.assert_awaited_once()
    redis.get.assert_not_awaited()


async def test_load_by_agent_empty_set_short_circuits() -> None:
    redis = AsyncMock()
    redis.smembers.return_value = []

    store = RedisFeedbackStore(redis, key_prefix="fb:")
    assert await store.load_by_agent("nobody") == []
    redis.mget.assert_not_awaited()
