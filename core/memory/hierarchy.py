"""
Hierarchical Memory System.

This module implements a sophisticated three-tier memory architecture
(Short-Term, Mid-Term, Long-Term) inspired by cognitive architectures
like MemoryOS. It handles the automatic promotion and consolidation
of information across different storage layers.
"""

import asyncio
from collections import deque
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import (
    Any,
)

from core.observability.logging import get_logger

from .embedding_compat import encode_flexible
from .hierarchy_config import HierarchyConfig, MemoryTier, TierConfig, TierStats
from .hierarchy_context import HierarchyContextMixin
from .hierarchy_search import HierarchySearchMixin
from .hierarchy_stats import HierarchyStatsMixin
from .hierarchy_tiers import HierarchyTiersMixin, new_ltm_container
from .lifecycle import (
    decay_prune_enabled,
    drop_duplicates,
    partition_expired,
    prune_low_relevance,
    select_promotable,
    summarize_items,
    ttl_enforcement_enabled,
)
from .tenant_state import TenantScopedState, current_memory_tenant
from .types import MemoryItem, MemoryType

logger = get_logger(__name__)

__all__ = [
    "HierarchicalMemory",
    "HierarchyConfig",
    "MemoryTier",
    "TierConfig",
    "TierStats",
]


class HierarchicalMemory(
    HierarchyTiersMixin,
    HierarchySearchMixin,
    HierarchyContextMixin,
    HierarchyStatsMixin,
):
    """
    Advanced three-tier hierarchical memory implementation.

    Orchestrates the lifecycle of information from immediate context (STM)
    to summarized mid-term clusters (MTM) and persistent, compressed
    archival storage (LTM).

    Tiers:
    - STM: Rolling FIFO buffer for immediate conversation state.
    - MTM: Summarized topic-clusters for medium-term relevance.
    - LTM: Provider-backed persistent storage for historical facts.

    Example:
        >>> hierarchy = HierarchicalMemory()
        >>> await hierarchy.add("User prefers dark mode")
        >>> context = await hierarchy.get_context(max_tokens=2000)

    Tenancy:
        Every tier is scoped to the tenant bound to the current context (see
        :mod:`core.memory.tenant_state`), so a store shared across requests
        cannot promote one tenant's context into another's prompt.
    """

    # Bounded deque per tenant: narrows the ``Iterable[MemoryItem]`` the
    # search/context mixins declare, exactly as the old ``__init__``
    # annotation did.
    _ltm: TenantScopedState[deque[MemoryItem]] = TenantScopedState(new_ltm_container)

    def __init__(
        self,
        config: HierarchyConfig | None = None,
        embedder: Any | None = None,
        llm_service: Any | None = None,
        provider: Any | None = None,
    ):
        """
        Initialize hierarchical memory.

        Args:
            config: Configuration for tier limits and behavior
            embedder: Optional embedder for semantic operations
            llm_service: Optional LLM for summarization
            provider: Optional persistent storage provider for LTM
        """
        self.config = config or HierarchyConfig()
        self.embedder = embedder
        self._llm_service = llm_service
        self.provider = provider

        # Tier storage lives on HierarchyTiersMixin as tenant-scoped
        # descriptors: one set of containers per tenant, created lazily on
        # first access. Nothing is initialized here — a store built at import
        # time has no tenant to resolve, and pre-creating containers would
        # both pick the wrong one and defeat the isolation.

        # Overflow-triggered maintenance runs as tracked background tasks
        # (single-flighted per tier) so an add() on the request path never
        # waits on consolidation — which cascades into MTM compression and
        # a full LLM summarization round trip.
        self._maintenance_tasks: dict[str, asyncio.Task] = {}

    def _schedule_maintenance(
        self, name: str, coro_factory: Callable[[], Coroutine[Any, Any, Any]]
    ) -> None:
        """Run tier maintenance in the background, single-flighted per tier.

        The task reference is retained (fire-and-forget tasks can be
        garbage-collected mid-run) and failures are logged via the done
        callback instead of vanishing.
        """
        # Single-flighted per tier *and per tenant*: the tiers are per-tenant,
        # so a name shared across tenants would let one tenant's in-flight
        # consolidation suppress another's — leaving that tenant's tier over
        # capacity until the first finished.
        name = f"{name}:{current_memory_tenant()}"
        existing = self._maintenance_tasks.get(name)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(coro_factory())
        self._maintenance_tasks[name] = task

        def _log_result(finished: asyncio.Task) -> None:
            if not finished.cancelled() and finished.exception() is not None:
                logger.error(
                    f"Memory maintenance '{name}' failed: {finished.exception()}"
                )

        task.add_done_callback(_log_result)

    async def wait_for_maintenance(self) -> None:
        """Await any in-flight background consolidation/compression.

        Deterministic hook for tests and graceful shutdown.
        """
        pending = [t for t in self._maintenance_tasks.values() if not t.done()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # ``recall`` offloads ``_fuse_recall`` to the inference thread pool, and the
    # tiers it reads are tenant-scoped through the tenant contextvar. This class
    # used to wrap the method in a ``contextvars.copy_context()`` shim so the
    # worker saw the caller's tenant; ``core.utils.concurrency.run_inference``
    # now takes that copy for every offload site, so the shim is gone and the
    # inherited ``HierarchySearchMixin._fuse_recall`` is used directly.

    @property
    def llm_service(self) -> Any | None:
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                pass
        return self._llm_service

    async def add(
        self,
        content: str,
        tier: MemoryTier = MemoryTier.STM,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
    ) -> MemoryItem:
        """
        Record a new memory into a specific hierarchy tier.

        Automatically handles metadata enrichment (importance, tier source)
        and dispatches to the appropriate tier-specific storage logic.

        Args:
            content: The text payload of the memory.
            tier: The target memory tier (default: STM).
            metadata: Optional dictionary of additional context.
            importance: Numerical score (0.0 to 1.0) indicating
                       significance for future recall and promotion.

        Returns:
            MemoryItem: The structured and timestamped memory object.
        """
        metadata = metadata or {}
        metadata["importance"] = importance
        metadata["tier"] = tier.value

        item = MemoryItem(
            content=content,
            memory_type=MemoryType.SHORT_TERM
            if tier == MemoryTier.STM
            else MemoryType.LONG_TERM,
            metadata=metadata,
        )

        if tier == MemoryTier.STM:
            await self._add_to_stm(item)
        elif tier == MemoryTier.MTM:
            await self._add_to_mtm(item)
        else:
            await self._add_to_ltm(item)

        return item

    async def _resolve_embedding(
        self,
        item: MemoryItem,
        cached: list[float] | None = None,
    ) -> list[float]:
        """Return ``cached`` if provided, else encode ``item.content``."""
        if cached is not None:
            return cached
        if not self.embedder:
            return []
        try:
            embedding: list[float] = await encode_flexible(self.embedder, item.content)
            return embedding
        except Exception as e:
            logger.warning(f"Failed to generate embedding: {e}")
            return []

    async def _add_to_stm(
        self,
        item: MemoryItem,
        embedding: list[float] | None = None,
    ) -> None:
        """Add item to short-term memory with FIFO eviction."""
        # Resolve before appending: the two lists are zipped under strict=True
        # by consolidation and the TTL sweep, and an ``await`` *between* the
        # appends is a cancellation point — a cancelled encode would leave a
        # permanent one-element skew that then raises out of every later
        # maintenance pass. ``list.append`` never suspends, so doing both
        # after the await makes the pair atomic.
        resolved = await self._resolve_embedding(item, embedding)
        self._stm.append(item)
        self._stm_embeddings.append(resolved)

        # Check capacity and auto-consolidate. Consolidation runs in the
        # background: it cascades into MTM compression (an LLM summarization
        # round trip), which must not stall the add() caller's request.
        if len(self._stm) > self.config.stm.max_items:
            if self.config.auto_consolidate:
                self._schedule_maintenance("stm_consolidate", self.consolidate_stm)
            else:
                # Simple FIFO eviction
                self._stm.pop(0)
                self._stm_embeddings.pop(0)

    async def _add_to_mtm(
        self,
        item: MemoryItem,
        embedding: list[float] | None = None,
    ) -> None:
        """Add item to mid-term memory with embedding cache."""
        # Same atomicity requirement as ``_add_to_stm`` above.
        resolved = await self._resolve_embedding(item, embedding)
        self._mtm.append(item)
        self._mtm_embeddings.append(resolved)

        # Check capacity and compress if needed. Compression summarizes via
        # the LLM — background task, never inline on the caller's request.
        if len(self._mtm) > self.config.mtm.max_items:
            if self.config.auto_consolidate:
                self._schedule_maintenance("mtm_compress", self.compress_mtm)
            else:
                # Remove oldest
                self._mtm.pop(0)
                self._mtm_embeddings.pop(0)

    async def _add_to_ltm(self, item: MemoryItem) -> None:
        """Add item to long-term memory.

        ``self._ltm`` is a ``deque(maxlen=ltm.max_items)``, so capacity-based
        eviction happens automatically on append.
        """
        self._ltm.append(item)

        # Persist to provider if available
        if self.provider:
            try:
                await self.provider.add(item)
            except Exception as e:
                logger.error(f"Failed to persist to LTM provider: {e}")

    async def consolidate_stm(self, items_to_migrate: int = 5) -> int:
        """
        Migrate the oldest entries from Short-Term Memory into Mid-Term storage.

        Implements cognitive consolidation where working context is
        processed into medium-term relevance. The oldest ``items_to_migrate``
        entries always leave STM (the working-set window keeps rolling), but
        only those whose importance clears ``stm.auto_promote_threshold``
        are promoted — the rest are evicted. Items whose normalized content
        already exists in MTM are dropped instead of duplicated. Expired MTM
        entries are swept first when a TTL is configured.

        Args:
            items_to_migrate: The number of items to shift out of STM.

        Returns:
            int: The actual count of items promoted into MTM.
        """
        if not self._stm:
            return 0

        self._sweep_expired_tiers()

        # Oldest items + their cached embeddings to skip re-encoding.
        leaving = list(
            zip(
                self._stm[:items_to_migrate],
                self._stm_embeddings[:items_to_migrate],
                strict=True,
            )
        )
        promotable, evicted = select_promotable(
            leaving, self.config.stm.auto_promote_threshold
        )
        unique, duplicates = drop_duplicates(promotable, self._mtm)

        migrated = 0
        for item, cached_embedding in unique:
            item.metadata["tier"] = MemoryTier.MTM.value
            item.metadata["promoted_at"] = datetime.now(UTC).isoformat()
            await self._add_to_mtm(
                item, embedding=cached_embedding if cached_embedding else None
            )
            migrated += 1

        # Remove from STM
        self._stm = self._stm[items_to_migrate:]
        self._stm_embeddings = self._stm_embeddings[items_to_migrate:]

        logger.info(
            f"Consolidated {migrated} items from STM to MTM "
            f"(evicted below threshold: {evicted}, duplicates dropped: {duplicates})"
        )
        return migrated

    async def compress_mtm(self, target_count: int | None = None) -> int:
        """
        Compress MTM by summarizing clusters and promoting to LTM.

        Uses LLM summarization when available, otherwise uses simple
        concatenation.

        Args:
            target_count: Target number of items after compression

        Returns:
            Number of items compressed
        """
        self._sweep_expired_tiers()

        if not self._mtm:
            return 0

        target = target_count or (self.config.mtm.max_items // 2)
        items_to_compress = len(self._mtm) - target

        if items_to_compress <= 0:
            return 0

        # Take oldest items for compression
        to_compress = self._mtm[:items_to_compress]

        # Create summary
        summary_content = await summarize_items(self.llm_service, to_compress)
        if summary_content:
            summary_item = MemoryItem(
                content=summary_content,
                memory_type=MemoryType.LONG_TERM,
                metadata={
                    "tier": MemoryTier.LTM.value,
                    "is_summary": True,
                    "source_count": len(to_compress),
                    "compressed_at": datetime.now(UTC).isoformat(),
                },
            )
            await self._add_to_ltm(summary_item)

        # Remove compressed items from MTM
        self._mtm = self._mtm[items_to_compress:]
        self._mtm_embeddings = self._mtm_embeddings[items_to_compress:]

        logger.info(f"Compressed {items_to_compress} MTM items into LTM summary")
        return items_to_compress

    def _sweep_expired_tiers(self) -> dict[str, int]:
        """Drop expired items from every tier per ``TierConfig.ttl_seconds``.

        No-op (empty counts) when TTL enforcement is disabled via
        ``BASELITH_MEMORY_TTL_ENFORCE=false`` or no tier declares a TTL.
        """
        counts = {"stm": 0, "mtm": 0, "ltm": 0}
        if not ttl_enforcement_enabled():
            return counts

        now = datetime.now(UTC)
        self._stm, self._stm_embeddings, counts["stm"] = partition_expired(
            self._stm, self._stm_embeddings, self.config.stm.ttl_seconds, now
        )
        self._mtm, self._mtm_embeddings, counts["mtm"] = partition_expired(
            self._mtm, self._mtm_embeddings, self.config.mtm.ttl_seconds, now
        )
        ltm_ttl = self.config.ltm.ttl_seconds
        if ltm_ttl is not None and self._ltm:
            alive, _, counts["ltm"] = partition_expired(
                list(self._ltm), None, ltm_ttl, now
            )
            if counts["ltm"]:
                self._ltm = deque(alive, maxlen=self.config.ltm.max_items)

        expired_total = sum(counts.values())
        if expired_total:
            logger.info(f"Memory TTL sweep evicted {expired_total} items: {counts}")
        if decay_prune_enabled():
            self.prune_low_relevance()
        return counts

    def purge_expired(self) -> dict[str, int]:
        """Evict TTL-expired items from all tiers. Returns counts per tier.

        Public hook for schedulers/operators; maintenance (consolidation and
        compression) already sweeps opportunistically. Provider-backed LTM
        persistence is not touched — the provider owns its own retention.
        """
        return self._sweep_expired_tiers()

    def prune_low_relevance(self, calculator: Any | None = None) -> dict[str, int]:
        """Drop MTM/LTM items whose decayed relevance classifies as prune.

        Applies the :class:`~core.memory.compression.RelevanceCalculator`
        policy (exponential age decay × importance, access boosts). Public
        hook for schedulers; runs automatically during maintenance sweeps
        when ``BASELITH_MEMORY_DECAY_PRUNE=true`` (default off).
        """
        return prune_low_relevance(self, calculator)

    def clear_all(self) -> dict[str, int]:
        """Clear all tier storage. Returns counts per tier."""
        counts = {
            "stm": len(self._stm),
            "mtm": len(self._mtm),
            "ltm": len(self._ltm),
        }
        self._stm.clear()
        self._stm_embeddings.clear()
        self._mtm.clear()
        self._mtm_embeddings.clear()
        self._ltm.clear()
        return counts
