"""
Supermemory Provider for BaselithCore.

This module implements the MemoryProvider protocol using Supermemory
(https://supermemory.ai) as the intelligent, persistent memory backend.

Key capabilities surfaced by this integration:
- Automatic fact extraction and temporal reasoning (facts expire/update naturally)
- Persistent user/agent profiles (static + dynamic context)
- Hybrid search: combines semantic vector search with personalized memory retrieval
- Multi-tenant isolation via Supermemory's containerTag mechanism
- Low-latency profile reads (~50ms) for prompt injection

The Supermemory SDK is synchronous: every call is therefore offloaded to a
worker thread (``asyncio.to_thread``) so the event loop is never blocked by a
network round-trip, and the client is built with the configured
``timeout_seconds`` / ``max_retries`` budget so an unresponsive endpoint fails
fast instead of hanging callers.

Usage:
    from core.memory.supermemory_provider import SupermemoryProvider, SupermemoryContextProvider
    from core.config.memory import get_supermemory_config

    config = get_supermemory_config()
    provider = SupermemoryProvider(container_tag="user_42")

    # Store a memory
    await provider.add(MemoryItem(content="User prefers dark mode", memory_type=MemoryType.ENTITY))

    # Search memories
    results = await provider.search("UI preferences")

    # Get enriched user profile for prompt injection
    ctx_provider = SupermemoryContextProvider(container_tag="user_42")
    context_str = await ctx_provider.get_context("programming style")
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.config.memory import SupermemoryConfig, get_supermemory_config
from core.memory.interfaces import ContextProvider, MemoryProvider
from core.memory.types import MemoryItem, MemoryType
from core.observability.logging import get_logger

logger = get_logger(__name__)

# Pre-fix releases wrote each MemoryType under its own ``{tag}_{suffix}``
# sub-tag while every read queried the bare tag, so nothing written was ever
# found again. Writes now go to the bare tag; these suffixes survive only so
# ``clear()`` can sweep what older releases left behind.
_LEGACY_TYPE_SUFFIXES: tuple[str, ...] = (
    "short",
    "long",
    "episodic",
    "entity",
    "general",
)

#: Upper bound the Supermemory search API accepts for ``limit``.
_MAX_SEARCH_LIMIT = 100

#: Rounds of search-and-forget a typed ``clear()`` runs before giving up.
_MAX_CLEAR_ROUNDS = 50


def _field_filter(key: str, value: str) -> dict[str, Any]:
    """Supermemory metadata filter matching ``metadata[key] == value``."""
    return {"AND": [{"key": key, "value": value}]}


def _hits(results: Any) -> list[Any]:
    """Memory hits from a search response (``memories`` or v4 ``results``)."""
    hits = getattr(results, "memories", None)
    if hits is None:
        hits = getattr(results, "results", None)
    return list(hits or [])


class SupermemoryProvider(MemoryProvider):
    """
    MemoryProvider implementation backed by Supermemory.

    Implements the standard BaselithCore MemoryProvider protocol so it can be
    used as a drop-in replacement for VectorMemoryProvider or InMemoryProvider
    anywhere an agent accepts a `provider` argument.

    Multi-tenancy is handled via Supermemory's containerTag mechanism: every
    memory of a tenant/agent lives under one container tag, so reads, deletes
    and the profile API all see what ``add()`` wrote. The ``MemoryType`` and
    the BaselithCore id travel in the memory's metadata and are matched with
    Supermemory metadata filters.

    Args:
        container_tag: Identifies the tenant/agent owning these memories.
                       Defaults to the configured `default_tag`.
        config: SupermemoryConfig instance. Falls back to the global singleton.
    """

    def __init__(
        self,
        container_tag: str | None = None,
        config: SupermemoryConfig | None = None,
    ) -> None:
        self._config = config or get_supermemory_config()
        self._container_tag = container_tag or self._config.default_tag
        self._client = self._build_client()

    def _build_client(self) -> Any:
        """Lazily construct the Supermemory SDK client."""
        try:
            from supermemory import Supermemory  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "The 'supermemory' package is required to use SupermemoryProvider. "
                "Install it with: pip install supermemory"
            ) from exc

        kwargs: dict = {
            "timeout": self._config.timeout_seconds,
            "max_retries": self._config.max_retries,
        }
        if self._config.api_key:
            kwargs["api_key"] = self._config.api_key.get_secret_value()
        if self._config.base_url:
            kwargs["base_url"] = self._config.base_url

        try:
            return Supermemory(**kwargs)
        except TypeError:
            # Older SDKs predate the timeout/max_retries constructor kwargs.
            kwargs.pop("timeout", None)
            kwargs.pop("max_retries", None)
            logger.warning(
                "Supermemory SDK predates timeout/max_retries kwargs; "
                "running with SDK defaults"
            )
            return Supermemory(**kwargs)

    async def _search_raw(
        self, query: str, limit: int, filters: dict[str, Any] | None = None
    ) -> list[Any]:
        """One ``search.memories`` call on this container tag, off the loop."""
        kwargs: dict[str, Any] = {
            "q": query,
            "container_tag": self._container_tag,
            "limit": min(limit, _MAX_SEARCH_LIMIT),
        }
        if filters is not None:
            kwargs["filters"] = filters
        results = await asyncio.to_thread(self._client.search.memories, **kwargs)
        return _hits(results)

    async def _find(self, item_id: str) -> Any | None:
        """The raw Supermemory memory whose metadata ``id`` is ``item_id``.

        Filtered on the stored id rather than ranked by similarity: a
        semantic search for a UUID with ``limit=1`` returned whichever memory
        happened to embed nearest, which was almost never the one asked for.
        The metadata id is re-checked in case a backend ignores the filter.
        """
        hits = await self._search_raw(
            item_id, self._config.search_limit, _field_filter("id", item_id)
        )
        for mem in hits:
            meta = getattr(mem, "metadata", {}) or {}
            if meta.get("id") == item_id:
                return mem
        return None

    # ------------------------------------------------------------------
    # MemoryProvider protocol
    # ------------------------------------------------------------------

    async def add(self, item: MemoryItem) -> None:
        """
        Store a MemoryItem in Supermemory.

        The item goes under this provider's container tag — the same tag
        every read and the profile API use — with its ``MemoryType`` and id
        in metadata so type-scoped searches and id lookups can filter on them.
        Caller metadata is forwarded but cannot override those keys.
        """
        tag = self._container_tag
        try:
            await asyncio.to_thread(
                self._client.add,
                content=item.content,
                container_tag=tag,
                metadata={
                    **item.metadata,
                    "id": str(item.id),
                    "memory_type": item.memory_type.value,
                    "created_at": item.created_at.isoformat(),
                },
            )
            logger.debug(
                "SupermemoryProvider: added memory",
                extra={"id": str(item.id), "tag": tag},
            )
        except Exception as exc:
            logger.error(f"SupermemoryProvider.add failed: {exc}")
            raise

    async def get(self, item_id: str) -> MemoryItem | None:
        """
        Retrieve a memory by its BaselithCore UUID.

        Supermemory has no lookup by caller-assigned id, so this is a search
        filtered on the ``id`` metadata field written by :meth:`add`.
        """
        try:
            mem = await self._find(item_id)
            return self._to_memory_item(mem) if mem is not None else None
        except Exception as exc:
            logger.error(f"SupermemoryProvider.get failed for {item_id}: {exc}")
            return None

    async def delete(self, item_id: str) -> bool:
        """
        Soft-delete a memory entry.

        Supermemory marks the memory as forgotten without permanent removal,
        preserving audit history.
        """
        try:
            mem = await self._find(item_id)
            sm_id = getattr(mem, "id", None) if mem is not None else None
            if not sm_id:
                return False
            await asyncio.to_thread(self._client.memories.forget, id=sm_id)
            logger.debug(
                f"SupermemoryProvider: forgot memory {item_id} (sm_id={sm_id})"
            )
            return True
        except Exception as exc:
            logger.error(f"SupermemoryProvider.delete failed for {item_id}: {exc}")
            return False

    async def search(
        self,
        query: str,
        memory_type: MemoryType | None = None,
        limit: int = 5,
        min_score: float = 0.0,
        query_vector: list[float] | None = None,
    ) -> list[MemoryItem]:
        """
        Hybrid semantic search across memories.

        ``query_vector`` is accepted for interface parity but ignored: the
        Supermemory backend embeds server-side from the raw ``query`` text.

        When `memory_type` is provided the search is filtered on the
        ``memory_type`` metadata field (and re-checked locally), mirroring the
        type-filtering semantics of the vector store backend. Otherwise it
        spans every memory type in the container.
        """
        effective_limit = limit or self._config.search_limit
        effective_min_score = min_score if min_score > 0.0 else self._config.min_score
        filters = (
            _field_filter("memory_type", memory_type.value)
            if memory_type is not None
            else None
        )

        try:
            memories = await self._search_raw(query, effective_limit, filters)
            items: list[MemoryItem] = []
            for mem in memories:
                score = float(getattr(mem, "score", 1.0) or 1.0)
                if score < effective_min_score:
                    continue
                item = self._to_memory_item(mem, fallback_type=memory_type)
                if memory_type is not None and item.memory_type != memory_type:
                    continue
                items.append(item)
            return items
        except Exception as exc:
            logger.error(f"SupermemoryProvider.search failed: {exc}")
            return []

    async def clear(self, memory_type: MemoryType | None = None) -> None:
        """
        Delete memories within this container (optionally scoped to a type).

        Without a type this is Supermemory's bulk delete on the container tag,
        plus a sweep of the per-type sub-tags older releases wrote to. With a
        type there is no bulk delete by metadata, so memories are found with
        a ``memory_type``-filtered search and forgotten one by one, in rounds,
        until a round finds nothing.
        """
        try:
            if memory_type is None:
                tags = [self._container_tag] + [
                    f"{self._container_tag}_{suffix}"
                    for suffix in _LEGACY_TYPE_SUFFIXES
                ]
                for tag in tags:
                    await asyncio.to_thread(
                        self._client.documents.delete_by_container, container_tag=tag
                    )
                    await asyncio.to_thread(
                        self._client.memories.delete_by_container, container_tag=tag
                    )
                logger.info(
                    f"SupermemoryProvider: cleared container '{self._container_tag}'"
                )
                return

            forgotten = 0
            filters = _field_filter("memory_type", memory_type.value)
            for _ in range(_MAX_CLEAR_ROUNDS):
                hits = await self._search_raw(
                    memory_type.value, _MAX_SEARCH_LIMIT, filters
                )
                ids = [getattr(m, "id", None) for m in hits]
                ids = [i for i in ids if i]
                if not ids:
                    break
                for sm_id in ids:
                    await asyncio.to_thread(self._client.memories.forget, id=sm_id)
                forgotten += len(ids)
            logger.info(
                f"SupermemoryProvider: forgot {forgotten} '{memory_type.value}' "
                f"memories in container '{self._container_tag}'"
            )
        except Exception as exc:
            logger.error(f"SupermemoryProvider.clear failed: {exc}")

    # ------------------------------------------------------------------
    # Supermemory-specific extras
    # ------------------------------------------------------------------

    async def get_profile(self, query: str | None = None) -> dict:
        """
        Retrieve the Supermemory user profile for this container tag.

        Returns a dict with `static` (long-lived facts) and `dynamic`
        (recent activity) fields — ready for direct prompt injection.

        This is a Supermemory-native capability with no equivalent in the
        standard MemoryProvider protocol. Callers that want richer context
        should cast the provider to SupermemoryProvider and call this directly,
        or use SupermemoryContextProvider which wraps it.

        Args:
            query: Optional search query to include targeted search results
                   alongside the profile.

        Returns:
            dict with 'static', 'dynamic', and optionally 'search_results' keys.
        """
        try:
            kwargs: dict = {"container_tag": self._container_tag}
            if query:
                kwargs["q"] = query

            result = await asyncio.to_thread(self._client.profile, **kwargs)
            profile = getattr(result, "profile", None)
            search_results = getattr(result, "search_results", [])

            return {
                "static": getattr(profile, "static", "") or "",
                "dynamic": getattr(profile, "dynamic", "") or "",
                "search_results": [
                    self._to_memory_item(m).to_dict() for m in (search_results or [])
                ],
            }
        except Exception as exc:
            logger.error(f"SupermemoryProvider.get_profile failed: {exc}")
            return {"static": "", "dynamic": "", "search_results": []}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_memory_item(
        self,
        mem: Any,
        fallback_type: MemoryType | None = None,
    ) -> MemoryItem:
        """Convert a raw Supermemory memory object to a BaselithCore MemoryItem."""
        meta: dict = getattr(mem, "metadata", {}) or {}
        content: str = getattr(mem, "content", "") or ""
        score: float = float(getattr(mem, "score", 1.0) or 1.0)

        # Recover MemoryType from stored metadata; fall back to the caller hint
        raw_type = meta.get("memory_type")
        if raw_type:
            try:
                mem_type = MemoryType(raw_type)
            except ValueError:
                mem_type = fallback_type or MemoryType.LONG_TERM
        else:
            mem_type = fallback_type or MemoryType.LONG_TERM

        return MemoryItem.from_dict(
            {
                "id": meta.get("id"),
                "content": content,
                "memory_type": mem_type.value,
                "created_at": meta.get("created_at"),
                "metadata": meta,
                "score": score,
            }
        )


class SupermemoryContextProvider(ContextProvider):
    """
    High-level context builder using Supermemory's profile API.

    Implements BaselithCore's ContextProvider ABC, producing a ready-to-use
    prompt string that combines the agent's long-lived profile facts with
    semantically relevant memory snippets for the current query.

    This is the recommended entry point when injecting memory context into
    LLM prompts, as it leverages Supermemory's optimised ~50ms profile reads.

    Args:
        container_tag: Tenant/agent identifier.
        config: SupermemoryConfig instance. Defaults to the global singleton.
        max_results: Maximum number of search results to include alongside the profile.
    """

    def __init__(
        self,
        container_tag: str | None = None,
        config: SupermemoryConfig | None = None,
        max_results: int = 3,
    ) -> None:
        self._provider = SupermemoryProvider(
            container_tag=container_tag,
            config=config,
        )
        self._max_results = max_results

    async def get_context(self, query: str, **_kwargs: Any) -> str:
        """
        Build a structured memory context string for prompt injection.

        The returned string contains:
        - [Profile] — stable facts about the user/agent (long-term identity)
        - [Recent activity] — dynamic recent context
        - [Relevant memories] — top search results for the query

        Args:
            query: The current user message or task description used to
                   retrieve targeted memory snippets.

        Returns:
            A formatted multi-section string ready to embed in a system prompt.
        """
        profile_data = await self._provider.get_profile(query=query)

        parts: list[str] = []

        static = (profile_data.get("static") or "").strip()
        if static:
            parts.append(f"[Profile]\n{static}")

        dynamic = (profile_data.get("dynamic") or "").strip()
        if dynamic:
            parts.append(f"[Recent activity]\n{dynamic}")

        search_results = profile_data.get("search_results") or []
        if search_results:
            snippets = "\n".join(
                f"- {r.get('content', '')}"
                for r in search_results[: self._max_results]
                if r.get("content")
            )
            if snippets:
                parts.append(f"[Relevant memories]\n{snippets}")

        return "\n\n".join(parts)
