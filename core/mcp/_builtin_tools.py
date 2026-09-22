"""Real implementations behind the adapter's built-in MCP tools.

Split out of :mod:`core.mcp.tools` for the module size cap, following the
``_``-prefixed sibling pattern the provider and service layers use.

These three tools used to return invented data. ``search_knowledge_base``
answered every query with one fabricated hit scored ``0.95``; ``index_document``
reported ``{"status": "indexed"}`` without writing anything; ``plan_task``
returned the same three generic steps ("Analyze requirements", "Break down into
subtasks", "Execute plan") for every task. All three were registered by
``register_all_tools`` and reached the app factory, so any deployment with the
MCP HTTP transport enabled served them to real clients.

That is worse than an unimplemented tool. The payload is well-formed either
way, so a model consuming this server cannot tell a placeholder from an answer
— it reads a confident fabrication as retrieved fact and cites it. Every
function here either does the work or reports that it could not; nothing
returns a shaped result it did not produce.

Tenant isolation is not re-implemented here: both retrieval paths go through
:class:`~core.services.vectorstore.service.VectorStoreService`, which resolves
the tenant from the ambient request context and hard-sets it on the query.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from core.config import get_mcp_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["index_document", "plan_task", "search_knowledge_base"]

#: The value the tool schemas advertise as the default ``collection``. It is a
#: sentinel, not a collection name: passing it through would search a
#: collection literally called "default" rather than the configured one.
DEFAULT_COLLECTION = "default"

#: Branching factor and depth for :func:`plan_task`. A planning call over MCP
#: is a request from a client the server does not control, so the search is
#: bounded to a small, predictable number of LLM calls rather than the engine's
#: 30-iteration default. The ambient ``LoopBudget`` remains the hard stop.
_PLAN_BRANCHING = 3
_PLAN_MAX_STEPS = 4
_PLAN_ITERATIONS = 6


def _collection_override(collection: str) -> str | None:
    """Translate the schema's sentinel into "use the configured collection"."""
    return None if collection == DEFAULT_COLLECTION else collection


async def _embed_query(query: str) -> list[float]:
    """Embed one query string without stalling the event loop.

    Constructing the embedder loads a sentence-transformer model, which is
    synchronous and slow, so it happens in a worker thread. ``encode`` itself
    is a coroutine function: handing it to ``to_thread`` would build a
    coroutine object in the thread, return it unawaited, and leave the caller
    indexing into a coroutine instead of a vector.

    Args:
        query: Text to embed.

    Returns:
        The query vector as plain floats.
    """

    def _load() -> Any:
        from core.nlp.models import get_embedder

        return get_embedder()

    embedder = await asyncio.to_thread(_load)
    encoded = embedder.encode([query])
    if inspect.isawaitable(encoded):
        encoded = await encoded
    return [float(value) for value in encoded[0]]


async def search_knowledge_base(
    query: str, top_k: int | None = None, collection: str = DEFAULT_COLLECTION
) -> list[dict[str, Any]]:
    """Search the knowledge base by semantic similarity.

    Args:
        query: Natural-language search query.
        top_k: Number of results to return; falls back to the MCP config.
        collection: Collection to search, or the ``default`` sentinel.

    Returns:
        One entry per hit with its id, content, score and metadata. A
        single-entry list carrying ``error`` when retrieval was unavailable —
        an empty corpus returns ``[]``, which is a different answer and is
        reported as such.
    """
    limit = top_k or get_mcp_config().mcp_rag_default_top_k

    try:
        from core.services.vectorstore.service import get_vectorstore_service

        vector = await _embed_query(query)
        results = await get_vectorstore_service().search(
            query_vector=vector,
            k=limit,
            collection_name=_collection_override(collection),
            query_text=query,
        )
    except Exception as exc:
        logger.error("mcp_search_knowledge_base_failed", error=str(exc))
        return [{"error": f"Knowledge base search unavailable: {exc}"}]

    return [
        {
            "id": result.document.id,
            "content": result.document.content,
            "score": result.score,
            "metadata": result.document.metadata,
        }
        for result in results
    ]


async def index_document(
    content: str,
    metadata: dict[str, Any] | None = None,
    collection: str = DEFAULT_COLLECTION,
) -> dict[str, Any]:
    """Index one document into the knowledge base.

    Args:
        content: Document text.
        metadata: Optional metadata stored alongside the chunks.
        collection: Target collection, or the ``default`` sentinel.

    Returns:
        The real outcome: ``chunks_written`` as reported by the vector store,
        or ``status: "error"`` with the reason. ``status`` is only ``indexed``
        when the store confirmed a write.
    """
    try:
        from core.models.domain import Document
        from core.services.vectorstore.service import get_vectorstore_service

        document = Document(content=content, metadata=dict(metadata or {}))
        written = await get_vectorstore_service().index(
            [document], collection_name=_collection_override(collection)
        )
    except Exception as exc:
        logger.error("mcp_index_document_failed", error=str(exc))
        return {"status": "error", "error": f"Indexing unavailable: {exc}"}

    if not written:
        return {
            "status": "error",
            "error": "Vector store accepted the request but wrote no chunks",
            "collection": collection,
            "content_length": len(content),
        }

    return {
        "status": "indexed",
        "collection": collection,
        "content_length": len(content),
        "chunks_written": written,
    }


async def plan_task(task_description: str, context: str = "") -> dict[str, Any]:
    """Plan a task by running the Tree-of-Thoughts search.

    The search is deliberately smaller than the engine's default: the caller is
    an MCP client the server does not control, and every expansion is a billed
    LLM call. The ambient request budget still applies and will abort the run
    before the bounds here are reached if it is the tighter limit.

    Args:
        task_description: The task to plan.
        context: Extra context folded into the problem statement.

    Returns:
        The derived steps and the winning solution, or ``status: "error"`` when
        no planner was available. It never returns a canned plan.
    """
    problem = (
        f"{task_description}\n\nContext: {context}" if context else task_description
    )

    try:
        from core.reasoning.tot.engine import TreeOfThoughts
        from core.services.llm import get_llm_service

        result = await TreeOfThoughts(llm_service=get_llm_service()).solve(
            problem,
            k=_PLAN_BRANCHING,
            max_steps=_PLAN_MAX_STEPS,
            iterations=_PLAN_ITERATIONS,
        )
    except Exception as exc:
        logger.error("mcp_plan_task_failed", error=str(exc))
        return {
            "task": task_description,
            "status": "error",
            "error": f"Planner unavailable: {exc}",
        }

    steps = [
        {"step": position, "description": description}
        for position, description in enumerate(result.get("steps") or [], start=1)
    ]
    if not steps:
        return {
            "task": task_description,
            "status": "error",
            "error": "Planner returned no steps",
        }

    return {
        "task": task_description,
        "steps": steps,
        "solution": result.get("solution"),
        "status": "planned",
    }
