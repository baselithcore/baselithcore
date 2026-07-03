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

from core.config.memory import SupermemoryConfig, get_supermemory_config
from core.memory.interfaces import ContextProvider, MemoryProvider
from core.memory.types import MemoryItem, MemoryType
from core.observability.logging import get_logger

logger = get_logger(__name__)

# MemoryType → Supermemory sub-tag suffix for scoped isolation within a container
_TYPE_SUFFIX: dict[MemoryType, str] = {
    MemoryType.SHORT_TERM: "short",
    MemoryType.LONG_TERM: "long",
    MemoryType.EPISODIC: "episodic",
    MemoryType.ENTITY: "entity",
}


def _build_tag(container_tag: str, memory_type: MemoryType | None) -> str:
    """Compose a scoped container tag from a base tag and optional memory type."""
    if memory_type is None:
        return container_tag
    suffix = _TYPE_SUFFIX.get(memory_type, "general")
    return f"{container_tag}_{suffix}"


class SupermemoryProvider(MemoryProvider):
    """
    MemoryProvider implementation backed by Supermemory.

    Implements the standard BaselithCore MemoryProvider protocol so it can be
    used as a drop-in replacement for VectorMemoryProvider or InMemoryProvider
    anywhere an agent accepts a `provider` argument.

    Multi-tenancy is handled via Supermemory's containerTag mechanism.
    Each (container_tag, MemoryType) pair maps to a distinct scoped tag,
    mirroring the isolation semantics of the framework's vector collections.

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

    def _build_client(self):
        """Lazily construct the Supermemory SDK client."""
        try:
            from supermemory import Supermemory  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "The 'supermemory' package is required to use SupermemoryProvider. "
                "Install it with: pip install supermemory"
            ) from exc

        kwargs: dict = {}
        if self._config.api_key:
            kwargs["api_key"] = self._config.api_key.get_secret_value()
        if self._config.base_url:
            kwargs["base_url"] = self._config.base_url

        return Supermemory(**kwargs)

    # ------------------------------------------------------------------
    # MemoryProvider protocol
    # ------------------------------------------------------------------

    async def add(self, item: MemoryItem) -> None:
        """
        Store a MemoryItem in Supermemory.

        The item is scoped to its MemoryType sub-tag so that type-filtered
        searches remain efficient. Metadata is forwarded as-is.
        """
        tag = _build_tag(self._container_tag, item.memory_type)
        try:
            self._client.add(
                content=item.content,
                container_tag=tag,
                metadata={
                    "id": str(item.id),
                    "memory_type": item.memory_type.value,
                    "created_at": item.created_at.isoformat(),
                    **item.metadata,
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

        Supermemory does not expose direct ID lookup across the SDK today,
        so we fall back to a metadata-filtered search on the stored `id` field.
        """
        try:
            results = self._client.search.memories(
                q=item_id,
                container_tag=self._container_tag,
                limit=1,
            )
            memories = getattr(results, "memories", []) or []
            for mem in memories:
                meta = getattr(mem, "metadata", {}) or {}
                if meta.get("id") == item_id:
                    return self._to_memory_item(mem)
            return None
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
            # Search for the memory to obtain its Supermemory-internal ID
            results = self._client.search.memories(
                q=item_id,
                container_tag=self._container_tag,
                limit=1,
            )
            memories = getattr(results, "memories", []) or []
            for mem in memories:
                meta = getattr(mem, "metadata", {}) or {}
                if meta.get("id") == item_id:
                    sm_id = getattr(mem, "id", None)
                    if sm_id:
                        self._client.memories.forget(id=sm_id)
                        logger.debug(
                            f"SupermemoryProvider: forgot memory {item_id} (sm_id={sm_id})"
                        )
                        return True
            return False
        except Exception as exc:
            logger.error(f"SupermemoryProvider.delete failed for {item_id}: {exc}")
            return False

    async def search(
        self,
        query: str,
        memory_type: MemoryType | None = None,
        limit: int = 5,
        min_score: float = 0.0,
    ) -> list[MemoryItem]:
        """
        Hybrid semantic search across memories.

        When `memory_type` is provided the search is scoped to the sub-tag
        for that type, mirroring the type-filtering semantics of the vector
        store backend. Otherwise the top-level container tag is used so the
        query spans all memory types for that agent/tenant.
        """
        tag = _build_tag(self._container_tag, memory_type)
        effective_limit = limit or self._config.search_limit
        effective_min_score = min_score if min_score > 0.0 else self._config.min_score

        try:
            results = self._client.search.memories(
                q=query,
                container_tag=tag,
                limit=effective_limit,
            )
            memories = getattr(results, "memories", []) or []
            items: list[MemoryItem] = []
            for mem in memories:
                score = float(getattr(mem, "score", 1.0) or 1.0)
                if score < effective_min_score:
                    continue
                item = self._to_memory_item(mem, fallback_type=memory_type)
                items.append(item)
            return items
        except Exception as exc:
            logger.error(f"SupermemoryProvider.search failed: {exc}")
            return []

    async def clear(self, memory_type: MemoryType | None = None) -> None:
        """
        Delete all memories within this container (optionally scoped to a type).

        Uses Supermemory's bulk delete on the container tag so only the
        targeted agent/type partition is affected.
        """
        tag = _build_tag(self._container_tag, memory_type)
        try:
            self._client.documents.delete_by_container(container_tag=tag)
            self._client.memories.delete_by_container(container_tag=tag)
            logger.info(f"SupermemoryProvider: cleared container '{tag}'")
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

            result = self._client.profile(**kwargs)
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
        mem,
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

    async def get_context(self, query: str, **_kwargs) -> str:
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
