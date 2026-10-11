"""ChatService wiring (service.py) and RagWorkflow/RagWorkflowHandler routing."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baselith.core.chat import service as service_module
from baselith.core.chat.dependencies import ChatDependencies, ChatDependencyConfig
from baselith.core.chat.guardrails import GuardrailDecision
from baselith.core.chat.rag_workflow import (
    RagWorkflow,
    RagWorkflowHandler,
    _search_wrapper,
)
from baselith.core.chat.service import (
    ChatService,
    _LazyChatServiceProxy,
    get_chat_service,
    initialize_chat_service_with_plugins,
)


def _deps(**overrides) -> ChatDependencies:
    fields = {
        "embedder": object(),
        "reranker": object(),
        "response_cache": MagicMock(name="response_cache"),
        "precheck_cache": None,
        "rerank_cache": MagicMock(name="rerank_cache"),
        "history_manager": MagicMock(name="history_manager"),
        "newline": "<nl>",
        "double_newline": "<nl><nl>",
        "section_separator": "<sep>",
    }
    fields.update(overrides)
    return ChatDependencies(**fields)  # type: ignore[arg-type]


class TestChatService:
    def test_injected_dependencies_are_exposed(self):
        deps = _deps()
        registry = object()
        svc = ChatService(
            dependencies=deps,
            dependency_config=ChatDependencyConfig(history_max_turns=7),
            plugin_registry=registry,
        )
        assert svc.dependencies is deps
        assert svc.embedder is deps.embedder
        assert svc.reranker is deps.reranker
        assert svc.history_manager is deps.history_manager
        assert svc.response_cache is deps.response_cache
        assert svc.rerank_cache is deps.rerank_cache
        assert svc.precheck_cache is None
        assert svc.plugin_registry is registry
        assert (svc.newline, svc.double_newline, svc.section_separator) == (
            "<nl>",
            "<nl><nl>",
            "<sep>",
        )
        assert svc.config.final_top_k == ChatService.FINAL_TOP_K
        assert svc.config.initial_search_k == ChatService.INITIAL_SEARCH_K
        assert svc.config.history_max_turns == 7

    def test_missing_dependencies_are_built_from_the_config(self):
        cfg = ChatDependencyConfig()
        with patch.object(
            service_module, "create_default_dependencies", return_value=_deps()
        ) as build:
            svc = ChatService(dependency_config=cfg)
        build.assert_called_once_with(cfg)
        assert svc.newline == "<nl>"


class TestServiceSingleton:
    @pytest.fixture(autouse=True)
    def fresh_singleton(self, monkeypatch):
        monkeypatch.setattr(service_module, "_chat_service", None)
        with patch.object(
            service_module,
            "create_default_dependencies",
            side_effect=lambda _c: _deps(),
        ) as build:
            yield build

    def test_lazy_creation_and_reuse(self, fresh_singleton):
        first = get_chat_service()
        assert get_chat_service() is first
        assert fresh_singleton.call_count == 1

    def test_plugin_registry_swaps_the_instance_only_when_it_changes(self):
        plain = get_chat_service()
        reg = object()
        with_reg = get_chat_service(reg)
        assert with_reg is not plain
        assert with_reg.plugin_registry is reg
        assert get_chat_service(reg) is with_reg
        # Without a registry, the current instance is returned as-is.
        assert get_chat_service() is with_reg

    def test_registry_creates_instance_when_none_exists(self):
        reg = object()
        initialize_chat_service_with_plugins(reg)
        assert service_module._chat_service is not None
        assert service_module._chat_service.plugin_registry is reg

    def test_proxy_resolves_on_first_attribute_access(self):
        proxy = _LazyChatServiceProxy()
        assert repr(proxy) == "<LazyChatServiceProxy unresolved>"
        assert proxy.newline == "<nl>"
        assert repr(proxy) == repr(service_module._chat_service)


class TestRagWorkflowSteps:
    @pytest.fixture
    def workflow(self):
        with patch("baselith.core.chat.rag_workflow.get_llm_service") as llm:
            llm.return_value.generate_response = AsyncMock()
            yield RagWorkflow(MagicMock())

    def test_fallback_decision_uses_response_or_default(self, workflow):
        for response, expected in (
            ("custom oos", "custom oos"),
            (None, "Posso rispondere solo a domande sui documenti indicizzati."),
        ):
            st = _agent_state()
            decision = GuardrailDecision(
                action="fallback", response=response, matched="m"
            )
            with patch(
                "baselith.core.chat.rag_workflow.evaluate_guardrails",
                return_value=decision,
            ):
                workflow.classify_intent(st)
            assert st.answer == expected
            assert st.done is True and st.next_action == ""
            assert st.logs == ["guardrail:fallback:m"]

    def test_block_without_response_uses_default(self, workflow):
        st = _agent_state()
        with patch(
            "baselith.core.chat.rag_workflow.evaluate_guardrails",
            return_value=GuardrailDecision(action="block"),
        ):
            workflow.classify_intent(st)
        assert st.answer == "Richiesta non consentita."
        assert st.logs == ["guardrail:block"]

    async def test_delegating_steps(self, workflow):
        st = _agent_state()
        workflow.planner = MagicMock()
        workflow.responder = MagicMock()
        workflow.retrieval = MagicMock(check_precheck_cache=AsyncMock())
        workflow.plan_backlog(st)
        workflow.finalize_answer(st)
        await workflow.check_precheck_cache(st)
        workflow.planner.plan_backlog.assert_called_once_with(st)
        workflow.responder.finalize_answer.assert_called_once_with(st)
        workflow.retrieval.check_precheck_cache.assert_awaited_once_with(st)

    def test_request_clarification_uses_the_clarifier_message(self, workflow):
        st = _agent_state()
        st.user_query = "lost?"
        workflow.request_clarification(st)
        assert st.done is True
        assert 'for the question "lost?".' in st.answer

    def test_search_wrapper_forwards_to_the_vector_store(self):
        with patch("baselith.core.chat.rag_workflow.get_vectorstore_service") as vs:
            vs.return_value.search.return_value = "hits"
            assert _search_wrapper([0.1], limit=3) == "hits"
        vs.return_value.search.assert_called_once_with(query_vector=[0.1], limit=3)


def _agent_state():
    from baselith.core.chat.agent_state import AgentState
    from baselith.core.models.chat import ChatRequest

    return AgentState(request=ChatRequest(query="q"))


class TestRagWorkflowHandlerRouting:
    @pytest.fixture
    def handler(self):
        with patch("baselith.core.chat.rag_workflow.get_llm_service"):
            handler = RagWorkflowHandler(MagicMock())
        wf = MagicMock()
        for name in (
            "load_history",
            "check_precheck_cache",
            "retrieve_documents",
            "score_documents",
            "apply_feedback",
            "build_context",
            "check_cache",
            "generate_answer",
        ):
            setattr(wf, name, AsyncMock())
        wf.validate_input.side_effect = lambda s: None
        wf.classify_intent.side_effect = lambda s: None
        handler._workflow = wf
        return handler

    async def test_request_fields_come_from_the_context(self, handler):
        seen = {}

        def validate(state):
            seen["request"] = state.request

        handler._workflow.validate_input.side_effect = validate
        await handler.handle("q", {"session_id": "s1", "tenant_id": "t1"})
        assert (seen["request"].conversation_id, seen["request"].tenant_id) == (
            "s1",
            "t1",
        )

    async def test_guardrail_stops_before_retrieval(self, handler):
        def classify(state):
            state.guardrail_decision = GuardrailDecision(action="block")
            state.answer = "no"
            state.done = True

        handler._workflow.classify_intent.side_effect = classify
        result = await handler.handle("q", {})
        assert result["response"] == "no"
        assert result["metadata"]["guardrail"] == "block"
        handler._workflow.load_history.assert_not_awaited()

    async def test_precheck_hit_skips_retrieval(self, handler):
        async def hit(state):
            state.answer = "cached"
            state.done = True

        handler._workflow.check_precheck_cache.side_effect = hit
        result = await handler.handle("q", {})
        assert result["response"] == "cached"
        handler._workflow.retrieve_documents.assert_not_awaited()

    async def test_retrieval_failure_requests_clarification(self, handler):
        async def fail(state):
            state.clarification_reason = "no_hits"

        def clarify(state):
            state.answer = "please clarify"

        handler._workflow.retrieve_documents.side_effect = fail
        handler._workflow.request_clarification.side_effect = clarify
        result = await handler.handle("q", {})
        assert result["response"] == "please clarify"
        assert result["metadata"] == {"guardrail": None, "clarification": "no_hits"}
        handler._workflow.score_documents.assert_not_awaited()

    async def test_response_cache_hit_skips_generation(self, handler):
        async def cached(state):
            state.answer = "from cache"
            state.done = True

        handler._workflow.check_cache.side_effect = cached
        result = await handler.handle("q", {})
        assert result["response"] == "from cache"
        handler._workflow.apply_feedback.assert_not_awaited()
        handler._workflow.generate_answer.assert_not_awaited()
        handler._workflow.plan_backlog.assert_not_called()
