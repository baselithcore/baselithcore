"""Tests for the opt-in pre-retrieval answer cache.

Covers the three things that make the layer safe to ship: a hit really does
skip retrieval/rerank, a miss changes nothing, and with the feature off the
pipeline behaves exactly as it did before the layer existed.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.chat.agent_state import AgentState
from core.chat.precheck import (
    PRECHECK_CACHE_NAMESPACE,
    PRECHECK_KEY_NAMESPACE,
    build_precheck_key,
)
from core.chat.rag_workflow import RagWorkflowHandler
from core.chat.service import ChatService
from core.chat.workflow_response import ResponseGenerator
from core.chat.workflow_retrieval import RetrievalPipeline
from core.models.chat import ChatRequest


@pytest.fixture
def mock_indexing():
    """Pin the corpus version so keys are deterministic."""
    with patch("core.chat.precheck.get_indexing_service") as mock:
        mock.return_value = MagicMock(index_version=7)
        yield mock


def make_service(*, precheck_cache=None):
    service = MagicMock(
        spec_set=[
            "INITIAL_SEARCH_K",
            "FINAL_TOP_K",
            "newline",
            "double_newline",
            "section_separator",
            "reranker",
            "rerank_cache",
            "response_cache",
            "precheck_cache",
            "history_manager",
            "embedder",
        ]
    )
    service.INITIAL_SEARCH_K = 3
    service.FINAL_TOP_K = 2
    service.newline = "\n"
    service.double_newline = "\n\n"
    service.section_separator = "---"
    service.reranker = MagicMock()
    service.rerank_cache = MagicMock()
    service.response_cache = AsyncMock()
    service.precheck_cache = precheck_cache
    service.history_manager = AsyncMock()
    service.history_manager.load.return_value = ([], "")
    service.embedder = AsyncMock()
    service.embedder.encode.return_value = [[0.1, 0.2]]
    return service


def make_pipeline(service):
    return RetrievalPipeline(
        service,
        search_fn=AsyncMock(return_value=[]),
        rerank_fn=MagicMock(return_value=[]),
        build_context_fn=MagicMock(return_value=("", [])),
    )


def make_state(query="what is the policy?", **request_kwargs):
    state = AgentState(request=ChatRequest(query=query, **request_kwargs))
    state.user_query = query
    state.normalized_query = query
    return state


# ---------------------------------------------------------------------------
# Key scheme: namespace, corpus version, tenant isolation
# ---------------------------------------------------------------------------


class TestPrecheckKey:
    def test_key_excludes_context_but_includes_history(self, mock_indexing):
        a = make_state()
        a.history_text = "User: hi"
        a.context = "CONTEXT A"

        b = make_state()
        b.history_text = "User: hi"
        b.context = "COMPLETELY DIFFERENT CONTEXT"

        # Same query + same history => same key regardless of context. That is
        # the whole point of the layer, and also the whole risk.
        assert build_precheck_key(a) == build_precheck_key(b)

        c = make_state()
        c.history_text = "User: something else"
        assert build_precheck_key(c) != build_precheck_key(a)

    def test_key_is_namespaced_apart_from_response_cache(self, mock_indexing):
        state = make_state()
        state.history_text = "hist"
        key = build_precheck_key(state)
        assert key is not None

        # The response cache hashes history + context with no namespace tag;
        # the pre-check payload is tagged, so the two key spaces cannot
        # collide even when the digests are compared directly.
        import hashlib

        untagged = hashlib.sha256(b"hist").hexdigest()
        assert key[1] != untagged
        assert PRECHECK_KEY_NAMESPACE == "rag_precheck:v1"

    def test_corpus_version_change_invalidates_the_key(self, mock_indexing):
        state = make_state()
        key_v7 = build_precheck_key(state)

        mock_indexing.return_value = MagicMock(index_version=8)
        key_v8 = build_precheck_key(state)

        assert key_v7 != key_v8

    def test_key_withheld_when_corpus_version_unavailable(self, mock_indexing):
        mock_indexing.side_effect = RuntimeError("indexing unavailable")
        assert build_precheck_key(make_state()) is None

    def test_tenant_and_kb_scope_the_key(self, mock_indexing):
        # Without the retrieved context in the key, nothing else would keep
        # one tenant's answer away from another's request.
        a = make_state(tenant_id="acme")
        b = make_state(tenant_id="globex")
        c = make_state(kb_label="handbook")
        base = make_state()

        keys = {
            build_precheck_key(a),
            build_precheck_key(b),
            build_precheck_key(c),
            build_precheck_key(base),
        }
        assert len(keys) == 4

    def test_rag_only_scopes_the_key(self, mock_indexing):
        plain = make_state()
        rag_only = make_state()
        rag_only.rag_only = True
        assert build_precheck_key(plain) != build_precheck_key(rag_only)


# ---------------------------------------------------------------------------
# Probe behaviour
# ---------------------------------------------------------------------------


class TestPrecheckProbe:
    async def test_hit_skips_retrieval_and_rerank(self, mock_indexing):
        cache = AsyncMock()
        cache.get.return_value = "cached answer"
        service = make_service(precheck_cache=cache)
        pipeline = make_pipeline(service)

        state = make_state()
        await pipeline.load_history(state)
        # The embedding is deferred past the probe, so a hit never pays for it.
        service.embedder.encode.assert_not_called()

        pipeline.retrieve_documents = AsyncMock()  # type: ignore[method-assign]
        pipeline.score_documents = AsyncMock()  # type: ignore[method-assign]

        await pipeline.check_precheck_cache(state)

        assert state.done is True
        assert state.answer == "cached answer"
        assert state.next_action == ""
        pipeline.retrieve_documents.assert_not_called()
        pipeline.score_documents.assert_not_called()
        pipeline.search_fn.assert_not_called()
        pipeline.rerank_fn.assert_not_called()
        pipeline.build_context_fn.assert_not_called()
        service.embedder.encode.assert_not_called()
        service.response_cache.get.assert_not_called()

    async def test_miss_proceeds_to_retrieval(self, mock_indexing):
        cache = AsyncMock()
        cache.get.return_value = None
        service = make_service(precheck_cache=cache)
        pipeline = make_pipeline(service)

        state = make_state()
        await pipeline.load_history(state)
        await pipeline.check_precheck_cache(state)

        assert state.done is False
        assert state.answer is None
        assert state.next_action == "retrieve_documents"
        # The deferred embedding is restored before retrieval needs it.
        assert state.query_vector == [0.1, 0.2]
        service.embedder.encode.assert_awaited_once()
        assert state.precheck_cache_key is not None

    async def test_cache_backend_failure_falls_through(self, mock_indexing):
        cache = AsyncMock()
        cache.get.side_effect = ConnectionError("redis down")
        service = make_service(precheck_cache=cache)
        pipeline = make_pipeline(service)

        state = make_state()
        await pipeline.load_history(state)
        await pipeline.check_precheck_cache(state)

        assert state.done is False
        assert state.next_action == "retrieve_documents"
        assert state.query_vector == [0.1, 0.2]

    async def test_missing_corpus_version_falls_through(self, mock_indexing):
        mock_indexing.side_effect = RuntimeError("boom")
        cache = AsyncMock()
        service = make_service(precheck_cache=cache)
        pipeline = make_pipeline(service)

        state = make_state()
        await pipeline.load_history(state)
        await pipeline.check_precheck_cache(state)

        cache.get.assert_not_called()
        assert state.precheck_cache_key is None
        assert state.next_action == "retrieve_documents"
        assert state.query_vector == [0.1, 0.2]


# ---------------------------------------------------------------------------
# Non-regression: feature OFF must be indistinguishable from before
# ---------------------------------------------------------------------------


class TestPrecheckDisabled:
    async def test_load_history_keeps_legacy_shape(self, mock_indexing):
        service = make_service(precheck_cache=None)
        pipeline = make_pipeline(service)

        state = make_state()
        await pipeline.load_history(state)

        # Same next hop and the same eager embedding as before the layer.
        assert state.next_action == "retrieve_documents"
        assert state.query_vector == [0.1, 0.2]
        service.embedder.encode.assert_awaited_once()

    async def test_probe_is_a_total_no_op(self, mock_indexing):
        service = make_service(precheck_cache=None)
        pipeline = make_pipeline(service)

        state = make_state()
        await pipeline.load_history(state)
        before = (state.next_action, state.done, state.answer, list(state.logs))

        await pipeline.check_precheck_cache(state)

        assert (state.next_action, state.done, state.answer, list(state.logs)) == before
        assert state.precheck_cache_key is None

    async def test_handler_pipeline_unchanged_when_disabled(self, mock_indexing):
        handler = RagWorkflowHandler(MagicMock())
        wf = MagicMock()
        handler._workflow = wf
        for step in (
            "load_history",
            "check_precheck_cache",
            "retrieve_documents",
            "score_documents",
            "build_context",
            "check_cache",
            "generate_answer",
        ):
            setattr(wf, step, AsyncMock())

        wf.validate_input.side_effect = lambda state: setattr(state, "done", False)
        wf.classify_intent.side_effect = lambda state: setattr(state, "done", False)

        await handler.handle("test query", context={})

        # Disabled, check_precheck_cache leaves `done` false, so every
        # downstream step still runs.
        wf.retrieve_documents.assert_awaited_once()
        wf.score_documents.assert_awaited_once()
        wf.build_context.assert_awaited_once()
        wf.check_cache.assert_awaited_once()

    async def test_handler_short_circuits_on_precheck_hit(self, mock_indexing):
        handler = RagWorkflowHandler(MagicMock())
        wf = MagicMock()
        handler._workflow = wf
        for step in (
            "load_history",
            "retrieve_documents",
            "score_documents",
            "build_context",
            "check_cache",
            "generate_answer",
        ):
            setattr(wf, step, AsyncMock())

        wf.validate_input.side_effect = lambda state: setattr(state, "done", False)
        wf.classify_intent.side_effect = lambda state: setattr(state, "done", False)

        async def _hit(state):
            state.answer = "cached answer"
            state.done = True

        wf.check_precheck_cache = AsyncMock(side_effect=_hit)

        result = await handler.handle("test query", context={})

        assert result["response"] == "cached answer"
        wf.retrieve_documents.assert_not_called()
        wf.score_documents.assert_not_called()
        wf.build_context.assert_not_called()
        wf.check_cache.assert_not_called()
        wf.generate_answer.assert_not_called()


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


class TestPrecheckWrite:
    def _generator(self, service):
        return ResponseGenerator(
            service,
            build_prompt_fn=MagicMock(return_value="prompt"),
            generate_response_fn=AsyncMock(return_value="fresh answer"),
        )

    async def test_answer_populates_both_cache_layers(self, mock_indexing):
        precheck = AsyncMock()
        service = make_service(precheck_cache=precheck)
        state = make_state()
        state.context = "grounded context"
        state.cache_key = ("q", "ctxhash")
        state.precheck_cache_key = ("q", "prehash")

        await self._generator(service).generate_answer(state)

        service.response_cache.set.assert_awaited_once_with(
            ("q", "ctxhash"), "fresh answer"
        )
        precheck.set.assert_awaited_once_with(("q", "prehash"), "fresh answer")

    async def test_ungrounded_answer_is_not_precached(self, mock_indexing):
        precheck = AsyncMock()
        service = make_service(precheck_cache=precheck)
        state = make_state()
        state.context = "   "
        state.cache_key = ("q", "ctxhash")
        state.precheck_cache_key = ("q", "prehash")

        await self._generator(service).generate_answer(state)

        # The response cache still records it (its key encodes the empty
        # context), but pinning a "found nothing" reply in the context-free
        # layer would survive the document being indexed.
        service.response_cache.set.assert_awaited_once()
        precheck.set.assert_not_called()

    async def test_no_write_when_layer_disabled(self, mock_indexing):
        service = make_service(precheck_cache=None)
        state = make_state()
        state.context = "grounded context"
        state.cache_key = ("q", "ctxhash")

        await self._generator(service).generate_answer(state)

        service.response_cache.set.assert_awaited_once()
        assert state.precheck_cache_key is None

    async def test_write_failure_does_not_break_the_request(self, mock_indexing):
        precheck = AsyncMock()
        precheck.set.side_effect = ConnectionError("redis down")
        service = make_service(precheck_cache=precheck)
        state = make_state()
        state.context = "grounded context"
        state.precheck_cache_key = ("q", "prehash")

        await self._generator(service).generate_answer(state)

        assert state.answer == "fresh answer"
        assert state.next_action == "finalize_answer"


# ---------------------------------------------------------------------------
# Wiring: separate TTL, separate namespace, default off
# ---------------------------------------------------------------------------


class TestPrecheckWiring:
    def test_disabled_by_default(self):
        from core.config.app import AppConfig

        config = AppConfig()
        assert config.chat_rag_precheck_enabled is False

    def test_ttl_is_separate_and_shorter_than_the_response_cache(self):
        from core.config.app import AppConfig

        config = AppConfig()
        assert config.chat_rag_precheck_ttl < config.chat_response_cache_ttl

    def test_no_cache_built_when_disabled(self):
        from core.chat.dependencies import (
            ChatDependencyConfig,
            create_default_dependencies,
        )

        cfg = ChatDependencyConfig(
            precheck_cache_enabled=False,
            embedder_factory=lambda _model: MagicMock(),
            reranker_factory=lambda _model: MagicMock(),
        )
        assert create_default_dependencies(cfg).precheck_cache is None

    def test_cache_gets_its_own_namespace_and_ttl(self):
        from core.chat.dependencies import (
            ChatDependencyConfig,
            create_default_dependencies,
        )

        built: dict[str, tuple[int, float]] = {}

        def response_factory(maxsize, ttl):
            built["response"] = (maxsize, ttl)
            return MagicMock()

        def precheck_factory(maxsize, ttl):
            built["precheck"] = (maxsize, ttl)
            return MagicMock()

        cfg = ChatDependencyConfig(
            precheck_cache_enabled=True,
            precheck_cache_ttl=42.0,
            precheck_cache_maxsize=11,
            response_cache_enabled=True,
            response_cache_ttl=3600.0,
            response_cache_maxsize=256,
            embedder_factory=lambda _model: MagicMock(),
            reranker_factory=lambda _model: MagicMock(),
            response_cache_factory=response_factory,
            precheck_cache_factory=precheck_factory,
        )
        deps = create_default_dependencies(cfg)

        assert deps.precheck_cache is not None
        assert deps.precheck_cache is not deps.response_cache
        assert built["precheck"] == (11, 42.0)
        assert built["response"] == (256, 3600.0)
        # Redis keys land under a dedicated prefix segment, so the layer can
        # be flushed without touching the response cache.
        assert PRECHECK_CACHE_NAMESPACE == "rag_precheck"

    def test_service_exposes_the_cache(self):
        assert hasattr(ChatService, "__init__")
        from core.services.chat.service import ChatService as CoreChatService

        service = CoreChatService(precheck_cache=None)
        assert service.precheck_cache is None
