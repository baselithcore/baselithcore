"""
Unit Tests for SimpleGraphMemoryProvider
"""

import pytest

from core.memory.graph_provider import SimpleGraphMemoryProvider


@pytest.mark.asyncio
async def test_add_relation_and_get_neighbors():
    provider = SimpleGraphMemoryProvider()
    await provider.add_relation("Alice", "works_at", "Google", weight=0.9)
    await provider.add_relation("Alice", "lives_in", "Zurich")

    neighbors = await provider.get_neighbors("Alice")
    assert len(neighbors) == 2

    work_rel = next(r for r in neighbors if r["relation"] == "works_at")
    assert work_rel["target"] == "Google"
    assert work_rel["weight"] == 0.9


@pytest.mark.asyncio
async def test_query_graph():
    provider = SimpleGraphMemoryProvider()
    await provider.add_relation("Alice", "works_at", "Google")

    # Query that mentions Alice
    results = await provider.query_graph("Tell me about Alice's job")
    assert len(results) == 1
    assert results[0]["source"] == "Alice"
    assert results[0]["target"] == "Google"


@pytest.mark.asyncio
async def test_update_relation():
    provider = SimpleGraphMemoryProvider()
    await provider.add_relation("Alice", "works_at", "Google", weight=0.5)
    await provider.add_relation("Alice", "works_at", "Google", weight=1.0)

    neighbors = await provider.get_neighbors("Alice", relation="works_at")
    assert len(neighbors) == 1
    assert neighbors[0]["weight"] == 1.0
