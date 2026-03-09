"""
Tests for core.chat modules.
"""

from dataclasses import fields


class TestAgentState:
    """Tests for AgentState dataclass."""

    def test_agent_state_import(self):
        """AgentState can be imported."""
        from core.chat.agent_state import AgentState

        assert AgentState is not None

    def test_agent_state_creation(self):
        """AgentState can be created with minimal args."""
        from core.chat.agent_state import AgentState

        class MockRequest:
            query = "test query"

        state = AgentState(request=MockRequest())
        assert state.request is not None
        assert state.user_query == ""
        assert not state.done

    def test_agent_state_has_expected_fields(self):
        """AgentState has all expected fields."""
        from core.chat.agent_state import AgentState

        field_names = {f.name for f in fields(AgentState)}

        expected = {
            "request",
            "user_query",
            "rerank_query",
            "normalized_query",
            "conversation_id",
            "history_turns",
            "history_text",
            "query_vector",
            "hits",
            "ranked_hits",
            "context",
            "doc_sources",
            "source_metrics",
            "cache_key",
            "answer",
            "done",
            "next_action",
            "logs",
            "guardrail_decision",
            "clarification_reason",
            "plugin_data",
            "rag_only",
        }
        assert expected.issubset(field_names)

    def test_agent_state_log_method(self):
        """AgentState.log() appends to logs."""
        from core.chat.agent_state import AgentState

        class MockRequest:
            query = "test"

        state = AgentState(request=MockRequest())
        state.log("test message")
        assert "test message" in state.logs

    def test_graph_state_import(self):
        """_GraphState can be imported."""
        from core.chat.agent_state import _GraphState

        assert _GraphState is not None


class TestGuardrails:
    """Tests for guardrails module."""

    def test_guardrail_decision_import(self):
        """GuardrailDecision can be imported."""
        from core.chat.guardrails import GuardrailDecision

        assert GuardrailDecision is not None

    def test_guardrail_decision_defaults(self):
        """GuardrailDecision has correct defaults."""
        from core.chat.guardrails import GuardrailDecision

        decision = GuardrailDecision()
        assert decision.action == "allow"
        assert decision.reason is None
        assert decision.response is None
        assert decision.matched is None

    def test_evaluate_guardrails_import(self):
        """evaluate_guardrails can be imported."""
        from core.chat.guardrails import evaluate_guardrails

        assert callable(evaluate_guardrails)

    def test_evaluate_guardrails_empty_query(self):
        """Empty query returns allow decision."""
        from core.chat.guardrails import evaluate_guardrails

        decision = evaluate_guardrails("")
        assert decision.action == "allow"


class TestPrompt:
    """Tests for prompt module."""

    def test_build_prompt_import(self):
        """build_prompt can be imported."""
        from core.chat.prompt import build_prompt

        assert callable(build_prompt)

    def test_conversation_system_prompt_exists(self):
        """CONVERSATION_SYSTEM_PROMPT is defined."""
        from core.chat.prompt import CONVERSATION_SYSTEM_PROMPT

        assert isinstance(CONVERSATION_SYSTEM_PROMPT, str)
        assert len(CONVERSATION_SYSTEM_PROMPT) > 100

    def test_build_prompt_basic(self):
        """build_prompt generates valid output."""
        from core.chat.prompt import build_prompt

        result = build_prompt(
            user_query="What is this?", context="Some context", history_text=""
        )
        assert "What is this?" in result
        assert "Some context" in result

    def test_build_prompt_with_history(self):
        """build_prompt includes history when provided."""
        from core.chat.prompt import build_prompt

        result = build_prompt(
            user_query="Follow up",
            context="Context",
            history_text="Previous conversation",
        )
        assert "Previous conversation" in result


class TestFeedback:
    """Tests for feedback module."""

    def test_apply_feedback_boost_import(self):
        """apply_feedback_boost can be imported."""
        from core.chat.feedback import apply_feedback_boost

        assert callable(apply_feedback_boost)

    def test_apply_feedback_boost_empty_hits(self):
        """Empty hits returns empty list."""
        from core.chat.feedback import apply_feedback_boost

        result = apply_feedback_boost(
            [], {}, min_total=1, positive_weight=0.1, negative_weight=0.05
        )
        assert result == []

    def test_apply_feedback_boost_no_feedback(self):
        """No feedback stats returns original order."""
        from core.chat.feedback import apply_feedback_boost

        class MockHit:
            id = "doc1"
            payload = {"document_id": "doc1"}

        hits = [(MockHit(), 0.8), (MockHit(), 0.5)]
        result = apply_feedback_boost(
            hits, {}, min_total=1, positive_weight=0.1, negative_weight=0.05
        )
        assert len(result) == 2


class TestStreaming:
    """Tests for streaming module."""

    def test_build_cached_stream_import(self):
        """build_cached_stream can be imported."""
        from core.chat.streaming import build_cached_stream

        assert callable(build_cached_stream)

    def test_build_cached_stream_yields_answer(self):
        """build_cached_stream yields the cached answer."""
        from core.chat.streaming import build_cached_stream

        stream = build_cached_stream("Hello world")
        chunks = list(stream)
        assert chunks == ["Hello world"]

    def test_build_fallback_stream_import(self):
        """build_fallback_stream can be imported."""
        from core.chat.streaming import build_fallback_stream

        assert callable(build_fallback_stream)

    def test_stream_answer_import(self):
        """stream_answer can be imported."""
        from core.chat.streaming import stream_answer

        assert callable(stream_answer)


class TestResponse:
    """Tests for response module."""

    def test_append_sources_import(self):
        """append_sources can be imported."""
        from core.chat.response import append_sources

        assert callable(append_sources)

    def test_append_sources_empty_sources(self):
        """Empty sources returns original answer."""
        from core.chat.response import append_sources

        result = append_sources("My answer", [])
        assert result == "My answer"

    def test_ensure_string_answer_import(self):
        """ensure_string_answer can be imported."""
        from core.chat.response import ensure_string_answer

        assert callable(ensure_string_answer)

    def test_ensure_string_answer_string_input(self):
        """String input returned as-is."""
        from core.chat.response import ensure_string_answer

        result = ensure_string_answer("hello")
        assert result == "hello"

    def test_ensure_string_answer_non_string(self):
        """Non-string input converted to string."""
        from core.chat.response import ensure_string_answer

        result = ensure_string_answer(123)
        assert result == "123"

    def test_strip_sources_section_import(self):
        """strip_sources_section can be imported."""
        from core.chat.response import strip_sources_section

        assert callable(strip_sources_section)


class TestContext:
    """Tests for context module."""

    def test_build_context_and_sources_import(self):
        """build_context_and_sources can be imported."""
        from core.chat.context import build_context_and_sources

        assert callable(build_context_and_sources)

    def test_build_context_empty_hits(self):
        """Empty hits returns empty context."""
        from core.chat.context import build_context_and_sources

        context, sources = build_context_and_sources(
            [],
            final_top_k=5,
            newline="\n",
            double_newline="\n\n",
            section_separator="\n---\n",
        )
        assert context == ""
        assert sources == []


class TestReranking:
    """Tests for reranking module."""

    def test_rerank_hits_import(self):
        """rerank_hits can be imported."""
        from core.chat.reranking import rerank_hits

        assert callable(rerank_hits)

    def test_protocols_exist(self):
        """Protocol classes exist."""
        from core.chat.reranking import CacheProtocol, RerankerProtocol

        assert CacheProtocol is not None
        assert RerankerProtocol is not None


class TestWorkflows:
    """Tests for workflows protocols."""

    def test_protocols_import(self):
        """Workflow protocols can be imported."""
        from core.chat.workflows import ValidatorProtocol, WorkflowStep

        assert ValidatorProtocol is not None
        assert WorkflowStep is not None
