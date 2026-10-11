"""Error and edge paths of RetrievalSearchMixin / RetrievalScoringMixin.

The happy paths live in test_workflow_retrieval.py; these pin the branches
that decide when the pipeline asks for clarification, how degraded
dependencies (vector store, qdrant-client, feedback DB) are absorbed, and how
conversation-preferred documents and per-document caps reshape the hits.
"""

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baselith.core.chat.agent_state import AgentState
from baselith.core.chat.mixins import retrieval_scoring as scoring
from baselith.core.chat.mixins import retrieval_search as search
from baselith.core.chat.workflow_retrieval import RetrievalPipeline
from baselith.core.models.chat import ChatRequest


def _hit(doc=None, hit_id="h", **payload):
    if doc is not None:
        payload["document_id"] = doc
    return SimpleNamespace(payload=payload, id=hit_id)


def _service(**overrides):
    fields = {
        "INITIAL_SEARCH_K": 10,
        "FINAL_TOP_K": 2,
        "reranker": object(),
        "rerank_cache": None,
        "_last_sources": {},
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _state(query="find it", vector=(0.1, 0.2)) -> AgentState:
    state = AgentState(request=ChatRequest(query=query))
    state.user_query = query
    state.query_vector = list(vector) if vector is not None else None
    return state


@pytest.fixture
def stores():
    """Patch the search mixin's indexing + vector store accessors."""
    vector_store = SimpleNamespace(
        query_points_groups=AsyncMock(return_value=SimpleNamespace(groups=[])),
        scroll=AsyncMock(return_value=([], None)),
    )
    indexing = SimpleNamespace(indexed_documents={}, index_version=None)
    with (
        patch.object(search, "get_vectorstore_service", return_value=vector_store),
        patch.object(search, "get_indexing_service", return_value=indexing),
    ):
        yield SimpleNamespace(vector_store=vector_store, indexing=indexing)


class TestRetrieveDocuments:
    async def test_missing_query_vector_returns_without_searching(self):
        search_fn = AsyncMock()
        state = _state(vector=None)
        await RetrievalPipeline(_service(), search_fn=search_fn).retrieve_documents(
            state
        )
        search_fn.assert_not_awaited()
        assert state.next_action == "validate_input"

    async def test_search_failure_requests_clarification(self):
        search_fn = AsyncMock(side_effect=ConnectionError("down"))
        state = _state()
        await RetrievalPipeline(_service(), search_fn=search_fn).retrieve_documents(
            state
        )
        assert state.clarification_reason == "retrieval_failed"
        assert state.next_action == "request_clarification"

    async def test_no_hits_requests_clarification(self, stores):
        state = _state()
        pipeline = RetrievalPipeline(_service(), search_fn=AsyncMock(return_value=[]))
        await pipeline.retrieve_documents(state)
        assert state.hits == []
        assert state.clarification_reason == "no_hits"
        assert "retrieval:empty" in state.logs

    async def test_conversation_sources_are_boosted_not_filtered(self, stores):
        hits = [
            _hit("other"),
            _hit(None, hit_id="by-id"),
            _hit(None, source=" prev.md "),
        ]
        service = _service(FINAL_TOP_K=1, _last_sources={"c1": {"prev.md", "by-id"}})
        state = _state()
        state.conversation_id = "c1"
        pipeline = RetrievalPipeline(service, search_fn=AsyncMock(return_value=hits))
        await pipeline.retrieve_documents(state)
        assert [h.id for h in state.hits][:2] == ["by-id", "h"]
        assert state.hits[2].payload["document_id"] == "other"
        assert state.next_action == "score_documents"

    async def test_fallback_query_failure_is_absorbed(self, stores):
        stores.indexing.indexed_documents = {"d1": {}, "d2": {}}
        stores.vector_store.query_points_groups.side_effect = RuntimeError("qdrant")
        pipeline = RetrievalPipeline(
            _service(), search_fn=AsyncMock(return_value=[_hit("d1")])
        )
        state = _state()
        await pipeline.retrieve_documents(state)
        assert [h.payload["document_id"] for h in state.hits] == ["d1"]
        assert state.next_action == "score_documents"

    async def test_fallback_without_seen_documents_has_no_filter(self, stores):
        stores.indexing.indexed_documents = {"d1": {}}
        extra = _hit("d9")
        stores.vector_store.query_points_groups.return_value = SimpleNamespace(
            groups=[SimpleNamespace(hits=[extra]), SimpleNamespace(hits=[])]
        )
        pipeline = RetrievalPipeline(
            _service(), search_fn=AsyncMock(return_value=[_hit(None, hit_id=1)])
        )
        state = _state()
        await pipeline.retrieve_documents(state)
        kwargs = stores.vector_store.query_points_groups.call_args.kwargs
        assert kwargs["query_filter"] is None and kwargs["limit"] == 1
        assert state.hits[-1] is extra


def test_limit_chunks_per_doc_keeps_unkeyed_hits():
    hits = [_hit("a"), _hit("a"), _hit("a"), _hit(None), _hit(None), _hit("b")]
    kept = search.RetrievalSearchMixin._limit_chunks_per_doc(hits)
    assert kept == [hits[0], hits[1], hits[3], hits[4], hits[5]]


class TestInjectExplicitDocMatches:
    @pytest.fixture(autouse=True)
    def _clean_memo(self):
        search._doc_match_cache.clear()
        yield
        search._doc_match_cache.clear()

    async def test_without_qdrant_client_uses_a_plain_dict_filter(self, stores):
        stores.indexing.indexed_documents = {
            "d1": {"metadata": {"filename": "budget.xlsx"}}
        }
        point = _hit("d1")
        stores.vector_store.scroll.return_value = (
            [point, _hit("d1"), _hit("d1")],
            None,
        )
        with patch.dict(sys.modules, {"qdrant_client.models": None}):
            out = await search.RetrievalSearchMixin._inject_explicit_doc_matches(
                "open budget.xlsx", []
            )
        assert len(out) == 2 and out[0] is point
        assert stores.vector_store.scroll.call_args.kwargs["scroll_filter"] == {
            "document_id": "d1"
        }

    async def test_scroll_failure_is_absorbed_per_document(self, stores):
        stores.indexing.indexed_documents = {
            "d1": {"metadata": {"title": "alpha"}},
            "d2": {"metadata": {"title": "beta"}},
        }
        good = _hit("d2")

        async def scroll(**kwargs):
            if kwargs["scroll_filter"].must[0].match.value == "d1":
                raise RuntimeError("boom")
            return [good], None

        stores.vector_store.scroll.side_effect = scroll
        base = [_hit("x")]
        out = await search.RetrievalSearchMixin._inject_explicit_doc_matches(
            "alpha and beta", base
        )
        assert out == base + [good]

    async def test_already_present_and_empty_results_keep_hits(self, stores):
        stores.indexing.indexed_documents = {"d1": {"metadata": {"title": "alpha"}}}
        base = [_hit("d1")]
        out = await search.RetrievalSearchMixin._inject_explicit_doc_matches(
            "alpha", base
        )
        assert out is base
        stores.vector_store.scroll.assert_not_awaited()

    async def test_stale_memo_entries_are_pruned(self, stores):
        stores.indexing.indexed_documents = {"live": {"metadata": {"title": "t"}}}
        for i in range(20):
            search._doc_match_cache[f"gone{i}"] = (
                ("", "", ""),
                "",
                "",
                "",
                frozenset(),
            )
        await search.RetrievalSearchMixin._inject_explicit_doc_matches("nothing", [])
        assert not any(key.startswith("gone") for key in search._doc_match_cache)

    async def test_index_failure_returns_the_hits_unchanged(self):
        base = [_hit("a")]
        with patch.object(
            search, "get_indexing_service", side_effect=RuntimeError("x")
        ):
            out = await search.RetrievalSearchMixin._inject_explicit_doc_matches(
                "q", base
            )
        assert out is base


@pytest.fixture
def scoring_stores():
    vector_store = SimpleNamespace(
        query_points_groups=AsyncMock(return_value=SimpleNamespace(groups=[]))
    )
    indexing = SimpleNamespace(indexed_documents={"d1": {}, "d2": {}})
    with (
        patch.object(scoring, "get_vectorstore_service", return_value=vector_store),
        patch.object(scoring, "get_indexing_service", return_value=indexing),
    ):
        yield vector_store


def _scoring_pipeline(ranked, **service):
    return RetrievalPipeline(
        _service(**service), rerank_fn=AsyncMock(return_value=ranked)
    )


class TestScoreDocuments:
    async def test_keeps_best_chunk_per_document(self, scoring_stores, monkeypatch):
        monkeypatch.setattr(scoring, "FEEDBACK_BOOST_ENABLED", False)
        a_hi, a_lo, b = _hit("a"), _hit("a"), _hit("b")
        anon = SimpleNamespace(payload=None, id=7)  # non-string id: skipped
        pipeline = _scoring_pipeline([(a_hi, 0.9), (anon, 0.99), (a_lo, 0.5), (b, 0.7)])
        state = _state()
        state.hits = [a_hi, a_lo, b, anon]
        await pipeline.score_documents(state)
        assert state.ranked_hits == [(a_hi, 0.9), (b, 0.7)]
        scoring_stores.query_points_groups.assert_not_awaited()
        assert state.next_action == "build_context"

    async def test_unkeyed_hits_skip_diversity_and_fallback_without_vector(
        self, scoring_stores, monkeypatch
    ):
        monkeypatch.setattr(scoring, "FEEDBACK_BOOST_ENABLED", True)
        anon = SimpleNamespace(payload=None, id=7)
        state = _state(vector=None)
        state.hits = [anon]
        await _scoring_pipeline([(anon, 0.3)]).score_documents(state)
        assert state.ranked_hits == [(anon, 0.3)]
        scoring_stores.query_points_groups.assert_not_awaited()
        assert state.next_action == "apply_feedback"

    async def test_fallback_without_seen_docs_and_with_scored_groups(
        self, scoring_stores
    ):
        anon = SimpleNamespace(payload={}, id=7)
        extra = SimpleNamespace(payload={"document_id": "d2"}, id="e", score=None)
        scoring_stores.query_points_groups.return_value = SimpleNamespace(
            groups=[SimpleNamespace(hits=[extra])]
        )
        state = _state()
        state.hits = [anon]
        await _scoring_pipeline([(anon, 0.3)]).score_documents(state)
        assert (
            scoring_stores.query_points_groups.call_args.kwargs["query_filter"] is None
        )
        assert state.ranked_hits[-1] == (extra, 0.0)

    async def test_fallback_failure_is_absorbed(self, scoring_stores):
        scoring_stores.query_points_groups.side_effect = RuntimeError("down")
        a = _hit("a")
        state = _state()
        state.hits = [a]
        await _scoring_pipeline([(a, 0.4)]).score_documents(state)
        assert state.ranked_hits == [(a, 0.4)]

    async def test_nothing_ranked_requests_clarification(self, scoring_stores):
        scoring_stores.query_points_groups.return_value = SimpleNamespace(groups=None)
        state = _state()
        await _scoring_pipeline([]).score_documents(state)
        assert state.clarification_reason == "no_reranked_hits"
        assert state.next_action == "request_clarification"
        assert "rerank:empty" in state.logs

    async def test_rerank_receives_capped_candidates_and_query(self, scoring_stores):
        rerank = AsyncMock(return_value=[])
        service = _service(INITIAL_SEARCH_K=2)
        state = _state()
        state.hits = [_hit(str(i)) for i in range(5)]
        state.rerank_query = "  rerank   me "
        await RetrievalPipeline(service, rerank_fn=rerank).score_documents(state)
        args, kwargs = rerank.call_args
        assert args[0] == "  rerank   me " and args[1] == "rerank me"
        assert len(args[2]) == min(2, scoring.RERANK_MAX_CANDIDATES)
        assert kwargs == {"reranker": service.reranker, "cache": None}


async def test_apply_feedback_boosts_with_db_summary(monkeypatch):
    summary = AsyncMock(
        return_value={"id::a": {"total": 9, "positives": 9, "negatives": 0}}
    )
    monkeypatch.setattr(scoring, "get_document_feedback_summary", summary)
    monkeypatch.setattr(scoring, "FEEDBACK_SCORE_MIN_TOTAL", 1)
    monkeypatch.setattr(scoring, "FEEDBACK_POSITIVE_WEIGHT", 0.1)
    a, b = _hit("a"), _hit("b")
    state = _state()
    state.ranked_hits = [(b, 0.5), (a, 0.2)]
    await RetrievalPipeline(MagicMock()).apply_feedback(state)
    summary.assert_awaited_once_with(min_total=1)
    assert [h for h, _ in state.ranked_hits] == [a, b]
    assert state.ranked_hits[0][1] == pytest.approx(1.1)
    assert state.next_action == "build_context"
