"""RetrievalContextMixin: history loading, embedding, context build, response cache."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baselith.core.chat.agent_state import AgentState
from baselith.core.chat.workflow_retrieval import RetrievalPipeline
from baselith.core.context import reset_tenant_context, set_tenant_context
from baselith.core.models.chat import ChatRequest

MODULE = "baselith.core.chat.mixins.retrieval_context"


@pytest.fixture
def service() -> SimpleNamespace:
    return SimpleNamespace(
        FINAL_TOP_K=2,
        newline="\n",
        double_newline="\n\n",
        section_separator="\n---\n",
        history_manager=SimpleNamespace(load=AsyncMock(return_value=([], ""))),
        embedder=object(),
        precheck_cache=None,
        response_cache=None,
    )


@pytest.fixture
def telemetry():
    with patch(f"{MODULE}.telemetry") as tel:
        yield tel


def _state(query: str = "what is X?", **request_kwargs) -> AgentState:
    state = AgentState(request=ChatRequest(query=query, **request_kwargs))
    state.user_query = query
    return state


def _hit(doc: str, text: str, **payload) -> SimpleNamespace:
    return SimpleNamespace(
        payload={"document_id": doc, "text": text, **payload}, id=doc
    )


class TestLoadHistory:
    async def test_without_history_embeds_and_routes_to_retrieval(self, service):
        encode = AsyncMock(return_value=[[0.1, 0.2]])
        state = _state("  what   is X?  ", conversation_id="   ")
        state.user_query = "what   is X?"
        with patch("baselith.core.nlp.roles.aencode_query", encode):
            await RetrievalPipeline(service).load_history(state)

        service.history_manager.load.assert_awaited_once_with(None)
        assert state.conversation_id is None
        assert state.rerank_query == "what   is X?"
        assert state.normalized_query == "what is X?"
        encode.assert_awaited_once_with(service.embedder, ["what   is X?"])
        assert state.query_vector == [0.1, 0.2]
        assert state.next_action == "retrieve_documents"

    async def test_recent_turns_are_appended_and_long_answers_truncated(self, service):
        turns = [
            {"query": "oldest", "answer": "dropped"},
            {"query": "", "answer": "a" * 250},
            {"query": "follow up", "answer": ""},
        ]
        service.history_manager.load = AsyncMock(return_value=(turns, "HIST"))
        state = _state("next?", conversation_id=" conv-1 ")
        state.query_vector = [1.0]  # already embedded: encoder must not run
        with patch("baselith.core.nlp.roles.aencode_query") as encode:
            await RetrievalPipeline(service).load_history(state)
        encode.assert_not_called()

        service.history_manager.load.assert_awaited_once_with("conv-1")
        assert state.history_turns == turns
        assert state.history_text == "HIST"
        assert state.rerank_query == (
            f"next?\nRecent context:\nAssistant: {'a' * 200}...\nUser: follow up"
        )
        assert "oldest" not in state.rerank_query

    async def test_precheck_cache_defers_embedding(self, service):
        service.precheck_cache = object()
        state = _state("q")
        with patch("baselith.core.nlp.roles.aencode_query") as encode:
            await RetrievalPipeline(service).load_history(state)
        encode.assert_not_called()
        assert state.query_vector is None
        assert state.next_action == "check_precheck_cache"


class TestBuildContext:
    async def test_metrics_from_real_context_builder(self, service, telemetry):
        state = _state()
        state.ranked_hits = [
            (_hit("a", "x" * 10, url="https://a"), 0.9),
            (_hit("b", "y" * 30, url="https://b"), 0.5),
            (_hit("c", "dropped by FINAL_TOP_K"), 0.4),
        ]
        await RetrievalPipeline(service).build_context(state)

        assert len(state.ranked_hits) == 2
        assert [s["document_id"] for s in state.doc_sources] == ["a", "b"]
        metrics = state.source_metrics
        assert metrics["total_context_ratio"] == pytest.approx(1.0)
        assert metrics["mean_context_ratio"] == pytest.approx(0.5)
        assert metrics["mean_score"] == pytest.approx(0.7)
        assert metrics["max_score"] == pytest.approx(0.9)
        assert metrics["top_context_ratio"] == pytest.approx(0.25)
        assert metrics["low_coverage"] is False
        assert "sources:top_ratio=0.25" in state.logs
        telemetry.increment.assert_not_called()
        assert state.next_action == "check_cache"

    async def test_low_coverage_is_flagged(self, service, telemetry):
        sources = [
            {"context_ratio": 0.1, "score": 0.3},
            {"context_ratio": "n/a", "score_avg": None, "score": "bad"},
        ]
        build = MagicMock(return_value=("ctx", sources))
        state = _state()
        await RetrievalPipeline(service, build_context_fn=build).build_context(state)

        build.assert_called_once_with(
            (),
            final_top_k=2,
            newline="\n",
            double_newline="\n\n",
            section_separator="\n---\n",
        )
        assert state.source_metrics == {
            "total_context_ratio": 0.1,
            "mean_context_ratio": 0.1,
            "mean_score": 0.3,
            "max_score": 0.3,
            "top_context_ratio": 0.1,
            "low_coverage": True,
        }
        telemetry.increment.assert_called_once_with("sources.low_coverage")

    async def test_sources_without_ratio_or_score(self, service):
        build = MagicMock(return_value=("ctx", [{"document_id": "a"}]))
        state = _state()
        await RetrievalPipeline(service, build_context_fn=build).build_context(state)
        assert state.source_metrics == {"low_coverage": False}

    async def test_empty_context_without_history_requests_clarification(self, service):
        build = MagicMock(return_value=("   ", []))
        state = _state()
        state.source_metrics = {"stale": True}
        await RetrievalPipeline(service, build_context_fn=build).build_context(state)
        assert state.source_metrics == {}
        assert state.clarification_reason == "empty_context"
        assert state.next_action == "request_clarification"
        assert "context:empty_without_history" in state.logs

    async def test_empty_context_with_history_still_answers(self, service):
        build = MagicMock(return_value=("", []))
        state = _state()
        state.history_text = "earlier turns"
        await RetrievalPipeline(service, build_context_fn=build).build_context(state)
        assert state.next_action == "check_cache"


class TestCheckCache:
    @pytest.mark.parametrize(("rag_only", "nxt"), [(False, "plan_backlog"), (True, "generate_answer")])  # fmt: skip
    async def test_no_cache_configured(self, service, rag_only, nxt):
        state = _state()
        state.rag_only = rag_only
        await RetrievalPipeline(service).check_cache(state)
        assert state.cache_key is None
        assert state.next_action == nxt

    async def test_hit_short_circuits_with_cached_answer(self, service, telemetry):
        service.response_cache = SimpleNamespace(get=AsyncMock(return_value="cached!"))
        state = _state()
        state.normalized_query = "q"
        state.context = "ctx"
        token = set_tenant_context("acme")
        try:
            await RetrievalPipeline(service).check_cache(state)
        finally:
            reset_tenant_context(token)
        assert state.answer == "cached!"
        assert state.done is True
        assert state.next_action == ""
        telemetry.increment.assert_any_call("response_cache.hit")
        telemetry.increment.assert_any_call("answers.cached")

    async def test_history_and_rag_only_change_the_context_hash(self, service):
        service.response_cache = SimpleNamespace(get=AsyncMock(return_value=None))
        keys = []
        token = set_tenant_context("acme")
        try:
            for history, rag_only in (("", False), ("h", False), ("h", True)):
                state = _state()
                state.normalized_query = "q"
                state.context = "ctx"
                state.history_text = history
                state.rag_only = rag_only
                await RetrievalPipeline(service).check_cache(state)
                keys.append(state.cache_key)
                expected = "generate_answer" if rag_only else "plan_backlog"
                assert state.next_action == expected
        finally:
            reset_tenant_context(token)
        assert all(k[:2] == ("acme", "q") for k in keys)
        assert len({k[2] for k in keys}) == 3

    async def test_missing_tenant_skips_the_cache(self, service, telemetry):
        from baselith.core.context import TenantContextError

        service.response_cache = SimpleNamespace(get=AsyncMock())
        state = _state()
        state.rag_only = True
        with patch(
            f"{MODULE}.get_current_tenant_id", side_effect=TenantContextError("none")
        ):
            await RetrievalPipeline(service).check_cache(state)
        service.response_cache.get.assert_not_awaited()
        assert state.cache_key is None
        assert state.next_action == "generate_answer"
        telemetry.increment.assert_called_once_with("response_cache.skipped_no_tenant")

    async def test_other_tenant_errors_surface(self, service):
        service.response_cache = SimpleNamespace(get=AsyncMock())
        with (
            patch(f"{MODULE}.get_current_tenant_id", side_effect=RuntimeError("bug")),
            pytest.raises(RuntimeError, match="bug"),
        ):
            await RetrievalPipeline(service).check_cache(_state())
