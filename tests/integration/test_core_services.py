"""
Integration tests for core services.

These tests verify that core services work correctly together.
Run with: pytest tests/integration/ -v --ignore=templates --ignore=examples
"""

import pytest
from unittest.mock import MagicMock, AsyncMock


class TestCacheIntegration:
    """Test caching layer isolation and functionality."""

    def test_redis_cache_prefix_isolation(self):
        """Verify that cache prefixes are correctly namespaced."""
        from core.optimization.caching import RedisCache

        cache1 = RedisCache(prefix="semantic")
        cache2 = RedisCache(prefix="embedding")

        # Verify different prefixes generate different keys
        key1 = cache1._make_key("test")
        key2 = cache2._make_key("test")

        assert key1 != key2
        assert ":semantic:" in key1
        assert ":embedding:" in key2

    def test_semantic_cache_hash_determinism(self):
        """Verify that semantic cache generates deterministic hashes."""
        from core.optimization.caching import SemanticCache

        cache = SemanticCache()

        hash1 = cache._hash_prompt("test query", model="gpt-4", temperature=0.7)
        hash2 = cache._hash_prompt("test query", model="gpt-4", temperature=0.7)
        hash3 = cache._hash_prompt("test query", temperature=0.7, model="gpt-4")

        # Same params -> same hash
        assert hash1 == hash2
        # Different order -> same hash (sorted keys)
        assert hash1 == hash3


class TestGraphDBCacheIntegration:
    """Test that GraphDB cache doesn't conflict with other caches."""

    def test_graph_cache_uses_correct_prefix(self):
        """Verify GraphDB cache uses isolated prefix."""
        from core.cache import RedisTTLCache

        mock_client = MagicMock()
        cache = RedisTTLCache(mock_client, prefix="agentbot:graph", default_ttl=3600)

        assert "agentbot:graph" in cache._prefix


class TestMemoryIntegration:
    """Test memory components integration."""

    def test_memory_manager_initialization(self):
        """Verify MemoryManager initializes correctly."""
        from core.memory.manager import AgentMemory

        manager = AgentMemory()

        assert manager is not None
        assert hasattr(manager, "_working_memory")
        assert hasattr(manager, "add_memory")

    @pytest.mark.asyncio
    async def test_memory_store_and_recall(self):
        """Test basic memory store and recall flow."""
        from core.memory.manager import AgentMemory
        from core.memory.types import MemoryType

        manager = AgentMemory()

        # Store a memory
        await manager.add_memory(
            content="Hello, this is a test message",
            memory_type=MemoryType.SHORT_TERM,
        )

        # Verify it's in the buffer
        assert len(manager._working_memory) > 0
        assert manager._working_memory[-1].content == "Hello, this is a test message"


class TestReasoningIntegration:
    """Test reasoning engine integration."""

    def test_tot_initialization(self):
        """Verify Tree of Thoughts initializes correctly."""
        from core.reasoning.tot import TreeOfThoughts

        tot = TreeOfThoughts()
        assert tot is not None

    @pytest.mark.asyncio
    async def test_tot_async_initialization(self):
        """Verify Async Tree of Thoughts initializes correctly."""
        from core.reasoning.tot import TreeOfThoughtsAsync

        mock_llm = MagicMock()
        mock_llm.generate = AsyncMock(return_value="Test thought")

        tot = TreeOfThoughtsAsync(llm_service=mock_llm)
        assert tot._llm_service is mock_llm


class TestOrchestratorServiceIntegration:
    """Test orchestrator with real service dependencies."""

    @pytest.mark.asyncio
    async def test_orchestrator_with_memory_integration(self):
        """Test orchestrator correctly uses memory manager."""
        from core.orchestration.orchestrator import Orchestrator
        from core.memory.manager import AgentMemory

        memory = AgentMemory()

        mock_classifier = MagicMock()

        orchestrator = Orchestrator(
            memory_manager=memory, intent_classifier=mock_classifier
        )

        assert orchestrator.memory_manager is memory

    @pytest.mark.asyncio
    async def test_orchestrator_flow_routing(self):
        """Test orchestrator routes to correct handlers."""
        from core.orchestration.orchestrator import Orchestrator
        from core.memory.manager import AgentMemory

        memory = AgentMemory()

        mock_classifier = MagicMock()
        mock_classifier.classify = AsyncMock(return_value="chat")

        orchestrator = Orchestrator(
            memory_manager=memory, intent_classifier=mock_classifier
        )

        mock_handler = AsyncMock()
        mock_handler.handle.return_value = {"content": "Response", "type": "final"}
        orchestrator._flow_handlers = {"chat": mock_handler}

        await orchestrator.process("Test input", context={"session_id": "test"})

        mock_handler.handle.assert_called_once()
