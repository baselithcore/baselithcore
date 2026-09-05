"""Query-aware context assembly: Background / Long-term sections are gated by
relevance to the current query instead of pure recency."""

from core.memory.hierarchy import HierarchicalMemory, MemoryTier


async def _memory_with_old_relevant_mtm() -> HierarchicalMemory:
    mem = HierarchicalMemory()  # no embedder → keyword path
    await mem.add("kubernetes rollout failed on node 3", tier=MemoryTier.MTM)
    for i in range(6):
        await mem.add(f"filler note number {i}", tier=MemoryTier.MTM)
    return mem


async def test_background_without_query_is_recency_only():
    mem = await _memory_with_old_relevant_mtm()
    context = mem.get_context(max_tokens=2000)
    # Only the last 5 MTM items are rendered: the old relevant note is gone.
    assert "kubernetes" not in context


async def test_background_with_query_surfaces_old_relevant_item():
    mem = await _memory_with_old_relevant_mtm()
    context = mem.get_context(max_tokens=2000, query="kubernetes rollout")
    assert "kubernetes rollout failed on node 3" in context
    # Relevant item leads the section.
    background = context.split("## Background", 1)[1]
    assert background.lstrip().startswith("- kubernetes rollout failed on node 3")


async def test_background_with_query_backfills_with_recent_items():
    mem = await _memory_with_old_relevant_mtm()
    context = mem.get_context(max_tokens=2000, query="kubernetes rollout")
    # One keyword hit + the most recent items fill the rest of the section.
    background = context.split("## Background", 1)[1]
    assert "filler note number 5" in background
    bullets = [line for line in background.splitlines() if line.startswith("- ")]
    assert len(bullets) == 5


async def test_long_term_summaries_with_query_surface_old_relevant_summary():
    mem = HierarchicalMemory()
    await mem.add(
        "[Summary] postgres vacuum tuning discussion",
        tier=MemoryTier.LTM,
        metadata={"is_summary": True},
    )
    for i in range(4):
        await mem.add(
            f"[Summary] unrelated topic {i}",
            tier=MemoryTier.LTM,
            metadata={"is_summary": True},
        )

    assert "postgres" not in mem.get_context(max_tokens=2000)
    assert "postgres vacuum" in mem.get_context(max_tokens=2000, query="postgres")


async def test_section_headings_sit_on_their_own_line():
    """Each ``## Heading`` is followed by a newline, as the docs show — a
    bullet never rides on the heading line."""
    mem = HierarchicalMemory()
    await mem.add("recent item", tier=MemoryTier.STM)
    await mem.add("background item", tier=MemoryTier.MTM)
    await mem.add("[Summary] old", tier=MemoryTier.LTM, metadata={"is_summary": True})

    context = mem.get_context(max_tokens=2000)

    assert "## Recent Context\n- recent item\n" in context
    assert "## Background\n- background item\n" in context
    assert "## Long-term Knowledge\n- [Summary] old\n" in context
