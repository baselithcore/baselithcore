"""The built-in MCP tools answer from the real services, or say they cannot.

The regression these guard is specific: all three tools used to return a
plausible, well-formed payload without doing any work — a fabricated hit scored
0.95, an ``indexed`` status for a document nothing wrote, three generic
planning steps. An MCP client has no way to distinguish that from an answer, so
each test below asserts both halves: the happy path carries the service's own
numbers, and the failure path carries an error rather than a shaped result.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.mcp import _builtin_tools
from core.models.domain import Document, SearchResult


@pytest.fixture
def embedder() -> Any:
    """Patch the embedder so no sentence-transformer model is loaded.

    ``CachedEmbedder.encode`` is a **coroutine function**, so the double is one
    too. A synchronous double hid a real defect: handing an async ``encode`` to
    ``asyncio.to_thread`` builds a coroutine object in the worker thread and
    returns it unawaited, and the caller then indexes into a coroutine.
    """
    fake = MagicMock()
    fake.encode = AsyncMock(return_value=[[0.1, 0.2, 0.3]])
    with patch("core.nlp.models.get_embedder", return_value=fake):
        yield fake


@pytest.fixture
def sync_embedder() -> Any:
    """A synchronous ``encode``, which the protocol also permits."""
    fake = MagicMock()
    fake.encode = MagicMock(return_value=[[0.1, 0.2, 0.3]])
    with patch("core.nlp.models.get_embedder", return_value=fake):
        yield fake


def _store(**methods: Any) -> Any:
    """Build a vector-store service double with the given async methods."""
    service = MagicMock()
    for name, value in methods.items():
        setattr(service, name, value)
    return service


class TestSearchKnowledgeBase:
    async def test_returns_the_stores_own_hits(self, embedder: Any) -> None:
        hit = SearchResult(
            document=Document(
                id="doc-7", content="Rome is the capital.", metadata={"source": "a.md"}
            ),
            score=0.42,
        )
        service = _store(search=AsyncMock(return_value=[hit]))

        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=service,
        ):
            results = await _builtin_tools.search_knowledge_base("capital of Italy")

        assert results == [
            {
                "id": "doc-7",
                "content": "Rome is the capital.",
                "score": 0.42,
                "metadata": {"source": "a.md"},
            }
        ]

    async def test_empty_corpus_returns_no_hits_not_a_placeholder(
        self, embedder: Any
    ) -> None:
        service = _store(search=AsyncMock(return_value=[]))

        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=service,
        ):
            results = await _builtin_tools.search_knowledge_base("anything")

        assert results == []

    async def test_store_failure_is_reported_not_invented(self, embedder: Any) -> None:
        service = _store(search=AsyncMock(side_effect=RuntimeError("qdrant down")))

        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=service,
        ):
            results = await _builtin_tools.search_knowledge_base("anything")

        assert len(results) == 1
        assert "qdrant down" in results[0]["error"]
        assert "score" not in results[0]

    async def test_default_collection_is_a_sentinel_not_a_name(
        self, embedder: Any
    ) -> None:
        """Forwarding the literal would search a collection called "default"."""
        search = AsyncMock(return_value=[])
        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=_store(search=search),
        ):
            await _builtin_tools.search_knowledge_base("q")
            await _builtin_tools.search_knowledge_base("q", collection="notes")

        assert search.await_args_list[0].kwargs["collection_name"] is None
        assert search.await_args_list[1].kwargs["collection_name"] == "notes"

    async def test_a_synchronous_embedder_also_works(self, sync_embedder: Any) -> None:
        search = AsyncMock(return_value=[])
        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=_store(search=search),
        ):
            results = await _builtin_tools.search_knowledge_base("q")

        assert results == []
        assert search.await_args.kwargs["query_vector"] == [0.1, 0.2, 0.3]

    async def test_the_query_vector_reaches_the_store(self, embedder: Any) -> None:
        """A coroutine forwarded instead of a vector would surface here."""
        search = AsyncMock(return_value=[])
        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=_store(search=search),
        ):
            await _builtin_tools.search_knowledge_base("q")

        assert search.await_args.kwargs["query_vector"] == [0.1, 0.2, 0.3]

    async def test_top_k_falls_back_to_config(self, embedder: Any) -> None:
        search = AsyncMock(return_value=[])
        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=_store(search=search),
        ):
            await _builtin_tools.search_knowledge_base("q", top_k=11)

        assert search.await_args.kwargs["k"] == 11


class TestIndexDocument:
    async def test_reports_the_chunk_count_the_store_wrote(self) -> None:
        index = AsyncMock(return_value=3)
        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=_store(index=index),
        ):
            result = await _builtin_tools.index_document("body", metadata={"k": "v"})

        assert result["status"] == "indexed"
        assert result["chunks_written"] == 3
        (documents,), _ = index.await_args
        assert documents[0].content == "body"
        assert documents[0].metadata == {"k": "v"}

    async def test_zero_chunks_written_is_not_success(self) -> None:
        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=_store(index=AsyncMock(return_value=0)),
        ):
            result = await _builtin_tools.index_document("body")

        assert result["status"] == "error"

    async def test_store_failure_is_reported(self) -> None:
        with patch(
            "core.services.vectorstore.service.get_vectorstore_service",
            return_value=_store(index=AsyncMock(side_effect=RuntimeError("no pool"))),
        ):
            result = await _builtin_tools.index_document("body")

        assert result["status"] == "error"
        assert "no pool" in result["error"]


class TestPlanTask:
    async def test_returns_the_planners_own_steps(self) -> None:
        engine = MagicMock()
        engine.solve = AsyncMock(
            return_value={
                "steps": ["Read the spec", "Write the test"],
                "solution": "ok",
            }
        )

        with (
            patch("core.reasoning.tot.engine.TreeOfThoughts", return_value=engine),
            patch("core.services.llm.get_llm_service", return_value=MagicMock()),
        ):
            result = await _builtin_tools.plan_task("ship it", context="repo is clean")

        assert result["status"] == "planned"
        assert result["steps"] == [
            {"step": 1, "description": "Read the spec"},
            {"step": 2, "description": "Write the test"},
        ]
        assert result["solution"] == "ok"
        assert "repo is clean" in engine.solve.await_args.args[0]

    async def test_search_is_bounded_for_an_untrusted_caller(self) -> None:
        engine = MagicMock()
        engine.solve = AsyncMock(return_value={"steps": ["a"], "solution": "ok"})

        with (
            patch("core.reasoning.tot.engine.TreeOfThoughts", return_value=engine),
            patch("core.services.llm.get_llm_service", return_value=MagicMock()),
        ):
            await _builtin_tools.plan_task("ship it")

        kwargs = engine.solve.await_args.kwargs
        assert kwargs["iterations"] < 30
        assert kwargs["max_steps"] <= 5

    async def test_planner_failure_is_reported_not_canned(self) -> None:
        with patch(
            "core.services.llm.get_llm_service", side_effect=RuntimeError("no provider")
        ):
            result = await _builtin_tools.plan_task("ship it")

        assert result["status"] == "error"
        assert "no provider" in result["error"]
        assert "steps" not in result

    async def test_empty_plan_is_an_error(self) -> None:
        engine = MagicMock()
        engine.solve = AsyncMock(return_value={"steps": [], "solution": "none"})

        with (
            patch("core.reasoning.tot.engine.TreeOfThoughts", return_value=engine),
            patch("core.services.llm.get_llm_service", return_value=MagicMock()),
        ):
            result = await _builtin_tools.plan_task("ship it")

        assert result["status"] == "error"
