"""
Unit Tests for Core Orchestration Module

Tests for the generic orchestration framework components:
- IntentClassifier
- BaseFlowHandler / BaseStreamHandler
- Orchestrator
"""

import pytest
from typing import Any, AsyncGenerator, Dict
from unittest.mock import MagicMock

from core.orchestration import (
    IntentClassifier,
    BaseFlowHandler,
    BaseStreamHandler,
    Orchestrator,
    FlowHandler,
    StreamHandler,
)


# ============================================================================
# IntentClassifier Tests
# ============================================================================


class TestIntentClassifier:
    """Tests for the generic IntentClassifier."""

    @pytest.mark.asyncio
    async def test_default_intent_when_no_patterns(self):
        """Should return default intent when no patterns match."""
        classifier = IntentClassifier(default_intent="fallback")
        result = await classifier.classify("random text without patterns")
        assert result == "fallback"

    @pytest.mark.asyncio
    async def test_register_and_match_intent(self):
        """Should match registered intent patterns."""
        classifier = IntentClassifier()
        classifier.register_intent("greeting", ["hello", "hi", "hey"])

        assert await classifier.classify("hello world") == "greeting"
        assert await classifier.classify("Hi there!") == "greeting"
        assert await classifier.classify("random text") == "qa_docs"  # default

    @pytest.mark.asyncio
    async def test_priority_ordering(self):
        """Higher priority intents should be checked first."""
        classifier = IntentClassifier()
        classifier.register_intent("low", ["test"], priority=1)
        classifier.register_intent("high", ["test"], priority=10)

        # Both match "test", but higher priority wins
        result = await classifier.classify("test query")
        assert result == "high"

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(self):
        """Pattern matching should be case-insensitive."""
        classifier = IntentClassifier()
        classifier.register_intent("greeting", ["HELLO"])

        assert await classifier.classify("hello") == "greeting"
        assert await classifier.classify("HELLO") == "greeting"
        assert await classifier.classify("Hello World") == "greeting"

    @pytest.mark.asyncio
    async def test_plugin_registry_loading(self):
        """Should load intents from plugin registry if provided."""
        mock_registry = MagicMock()
        mock_registry.get_all_intent_patterns.return_value = {
            "plugin_intent": {"patterns": ["plugin", "external"]}
        }

        classifier = IntentClassifier(plugin_registry=mock_registry)
        result = await classifier.classify("this is a plugin test")
        assert result == "plugin_intent"


# ============================================================================
# Handler Tests
# ============================================================================


class ConcreteFlowHandler(BaseFlowHandler):
    """Concrete implementation for testing."""

    async def handle(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "response": f"Handled: {query}",
            "context_keys": list(context.keys()),
        }


class ConcreteStreamHandler(BaseStreamHandler):
    """Concrete implementation for testing."""

    async def handle(
        self, query: str, context: Dict[str, Any]
    ) -> AsyncGenerator[str, None]:
        yield "token1"
        yield "token2"
        yield "token3"
        # BaseStreamHandler.handle protocol returns AsyncGenerator,
        # but the actual handler can have a return value used by orchestrator via error handling/wrapping
        # However, for testing consistency with new AsyncGenerator protocol:
        pass


class TestBaseFlowHandler:
    """Tests for BaseFlowHandler."""

    @pytest.mark.asyncio
    async def test_handle_returns_result(self):
        """Handler should process query and return result."""
        handler = ConcreteFlowHandler()
        result = await handler.handle("test query", {"key": "value"})

        assert result["response"] == "Handled: test query"
        assert "key" in result["context_keys"]

    def test_get_agent_and_service(self):
        """Should retrieve agents and services by name."""
        mock_agent = MagicMock()
        mock_service = MagicMock()

        handler = ConcreteFlowHandler(
            agents={"rag": mock_agent},
            services={"chat": mock_service},
        )

        assert handler.get_agent("rag") is mock_agent
        assert handler.get_service("chat") is mock_service
        assert handler.get_agent("nonexistent") is None


class TestBaseStreamHandler:
    """Tests for BaseStreamHandler."""

    @pytest.mark.asyncio
    async def test_handle_yields_tokens(self):
        """Stream handler should yield tokens progressively."""
        handler = ConcreteStreamHandler()
        gen = handler.handle("test", {})

        tokens = []
        async for token in gen:
            tokens.append(token)

        assert tokens == ["token1", "token2", "token3"]

    def test_yield_status_helper(self):
        """Should format status updates correctly."""
        status = BaseStreamHandler.yield_status("Processing...")
        assert status == "[STATUS] Processing..."

    def test_yield_progress_helper(self):
        """Should format progress updates correctly."""
        progress = BaseStreamHandler.yield_progress(1, 4, "Step 1")
        assert "[PROGRESS] 25%" in progress
        assert "Step 1" in progress


# ============================================================================
# Orchestrator Tests
# ============================================================================


class TestOrchestrator:
    """Tests for the Orchestrator base class."""

    def test_register_and_get_handlers(self):
        """Should register and retrieve handlers."""
        orchestrator = Orchestrator()
        handler = ConcreteFlowHandler()

        orchestrator.register_handler("test_intent", handler)

        assert "test_intent" in orchestrator.get_registered_intents()

    @pytest.mark.asyncio
    async def test_process_routes_to_handler(self):
        """Should route query to correct handler based on intent."""
        orchestrator = Orchestrator()
        handler = ConcreteFlowHandler()

        orchestrator.register_handler("qa_docs", handler)
        orchestrator.intent_classifier.register_intent("qa_docs", ["question"])

        result = await orchestrator.process("question about docs")

        assert result["response"] == "Handled: question about docs"
        assert result["intent"] == "qa_docs"

    @pytest.mark.asyncio
    async def test_process_returns_error_for_unknown_intent(self):
        """Should return error when no handler for intent."""
        orchestrator = Orchestrator()
        orchestrator.intent_classifier.register_intent("unknown", ["unknown"])

        result = await orchestrator.process("unknown request")

        assert result["error"] is True
        assert "No handler" in result["response"]

    @pytest.mark.asyncio
    async def test_process_stream_yields_tokens(self):
        """Should stream tokens from stream handler."""
        orchestrator = Orchestrator()
        flow_handler = ConcreteFlowHandler()
        stream_handler = ConcreteStreamHandler()

        orchestrator.register_handler("qa_docs", flow_handler, stream_handler)
        orchestrator.intent_classifier.register_intent("qa_docs", ["test"])

        tokens = []
        async for token in orchestrator.process_stream("test query"):
            tokens.append(token)

        assert "token1" in tokens
        assert "token2" in tokens

    def test_has_stream_handler(self):
        """Should correctly report stream handler availability."""
        orchestrator = Orchestrator()
        orchestrator.register_handler(
            "with_stream", ConcreteFlowHandler(), ConcreteStreamHandler()
        )
        orchestrator.register_handler("without_stream", ConcreteFlowHandler())

        assert orchestrator.has_stream_handler("with_stream") is True
        assert orchestrator.has_stream_handler("without_stream") is False


# ============================================================================
# Protocol Compliance Tests
# ============================================================================


class TestProtocolCompliance:
    """Tests to verify protocol implementations."""

    def test_flow_handler_protocol(self):
        """ConcreteFlowHandler should satisfy FlowHandler protocol."""
        handler = ConcreteFlowHandler()
        assert isinstance(handler, FlowHandler)

    def test_stream_handler_protocol(self):
        """ConcreteStreamHandler should satisfy StreamHandler protocol."""
        handler = ConcreteStreamHandler()
        assert isinstance(handler, StreamHandler)
