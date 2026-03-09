import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.chat.rag_workflow import RagWorkflow, RagWorkflowHandler
from core.chat.agent_state import AgentState
from core.models.chat import ChatRequest
from core.chat.guardrails import GuardrailDecision


class TestRagWorkflow:
    @pytest.fixture
    def mock_service(self):
        return MagicMock()

    @pytest.fixture
    def workflow(self, mock_service):
        with patch("core.chat.rag_workflow.get_llm_service") as mock_llm:
            mock_llm.return_value.generate_response = AsyncMock()
            return RagWorkflow(mock_service)

    def test_validate_input(self, workflow):
        state = AgentState(request=ChatRequest(query="test"))
        workflow.validator = MagicMock()
        workflow.validate_input(state)
        workflow.validator.validate_input.assert_called_once_with(state)

    def test_classify_intent_allow(self, workflow):
        state = AgentState(request=ChatRequest(query="safe query"))
        decision = GuardrailDecision(action="allow")

        with patch("core.chat.rag_workflow.evaluate_guardrails", return_value=decision):
            workflow.classify_intent(state)

        assert state.guardrail_decision == decision
        assert state.next_action == "prepare_query"
        assert not state.done

    def test_classify_intent_block(self, workflow):
        state = AgentState(request=ChatRequest(query="evil query"))
        decision = GuardrailDecision(
            action="block", matched="test_rule", response="Blocked."
        )

        with patch("core.chat.rag_workflow.evaluate_guardrails", return_value=decision):
            workflow.classify_intent(state)

        assert state.done
        assert state.answer == "Blocked."
        assert state.next_action == ""

    def test_prepare_query(self, workflow):
        state = AgentState(request=ChatRequest(query="test"))
        workflow.prepare_query(state)
        assert state.next_action == "load_history"

    async def test_async_steps(self, workflow):
        state = AgentState(request=ChatRequest(query="test"))

        workflow.retrieval = AsyncMock()
        workflow.responder = AsyncMock()

        await workflow.load_history(state)
        workflow.retrieval.load_history.assert_called_once_with(state)

        await workflow.retrieve_documents(state)
        workflow.retrieval.retrieve_documents.assert_called_once_with(state)

        await workflow.score_documents(state)
        workflow.retrieval.score_documents.assert_called_once_with(state)

        await workflow.apply_feedback(state)
        workflow.retrieval.apply_feedback.assert_called_once_with(state)

        await workflow.build_context(state)
        workflow.retrieval.build_context.assert_called_once_with(state)

        await workflow.check_cache(state)
        workflow.retrieval.check_cache.assert_called_once_with(state)

        await workflow.generate_answer(state)
        workflow.responder.generate_answer.assert_called_once_with(state)


class TestRagWorkflowHandler:
    @pytest.fixture
    def mock_service(self):
        return MagicMock()

    @pytest.mark.asyncio
    async def test_handle_full_pipeline(self, mock_service):
        handler = RagWorkflowHandler(mock_service)
        # Mock the internal workflow methods
        handler._workflow = MagicMock()
        handler._workflow.load_history = AsyncMock()
        handler._workflow.retrieve_documents = AsyncMock()
        handler._workflow.score_documents = AsyncMock()
        handler._workflow.build_context = AsyncMock()
        handler._workflow.check_cache = AsyncMock()
        handler._workflow.generate_answer = AsyncMock()

        # Initial state setup
        def mock_validate(state):
            state.done = False

        handler._workflow.validate_input.side_effect = mock_validate

        def mock_classify(state):
            state.done = False

        handler._workflow.classify_intent.side_effect = mock_classify

        result = await handler.handle("test query", context={})

        assert "response" in result
        assert "sources" in result
        assert handler._workflow.validate_input.called
        assert handler._workflow.classify_intent.called
        assert handler._workflow.load_history.called
        assert handler._workflow.retrieve_documents.called
        assert handler._workflow.score_documents.called

    @pytest.mark.asyncio
    async def test_handle_guardrail_block(self, mock_service):
        handler = RagWorkflowHandler(mock_service)
        handler._workflow = MagicMock()

        def mock_validate(state):
            state.done = True
            state.answer = "Blocked by validator"

        handler._workflow.validate_input.side_effect = mock_validate

        result = await handler.handle("bad query", context={})
        assert result["response"] == "Blocked by validator"
        assert not handler._workflow.classify_intent.called

    def test_to_result(self, mock_service):
        state = AgentState(request=ChatRequest(query="test"))
        state.answer = "Test response"
        state.doc_sources = [{"source": "doc1", "content": "text"}]
        state.guardrail_decision = GuardrailDecision(action="allow")
        state.clarification_reason = "More info needed"

        result = RagWorkflowHandler._to_result(state)
        assert result["response"] == "Test response"
        assert result["sources"] == [{"source": "doc1", "content": "text"}]
        assert result["metadata"]["guardrail"] == "allow"
        assert result["metadata"]["clarification"] == "More info needed"
