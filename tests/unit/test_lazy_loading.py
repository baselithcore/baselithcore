"""
Unit tests for lazy loading system.

Tests the LazyServiceRegistry, ResourceAnalyzer, and conditional initialization.
"""

import asyncio
import pytest
from pathlib import Path

from core.di.lazy_registry import (
    LazyServiceRegistry,
    ResourceType,
    get_lazy_registry,
    reset_lazy_registry,
)
from core.plugins.resource_analyzer import ResourceAnalyzer
from core.plugins.interface import PluginMetadata


# === Test Lazy Service Registry ===


class TestLazyServiceRegistry:
    """Test lazy service registry functionality."""

    def setup_method(self):
        """Reset registry before each test."""
        reset_lazy_registry()

    @pytest.mark.asyncio
    async def test_lazy_initialization(self):
        """Test that services are only initialized when requested."""
        registry = LazyServiceRegistry()
        call_count = 0

        async def create_service():
            nonlocal call_count
            call_count += 1
            return {"initialized": True, "count": call_count}

        # Register factory
        registry.register_factory("test_service", create_service)

        # Should not be initialized yet
        assert not registry.is_initialized("test_service")
        assert call_count == 0

        # First call should initialize
        result1 = await registry.get_or_create("test_service")
        assert result1["initialized"] is True
        assert call_count == 1
        assert registry.is_initialized("test_service")

        # Second call should return same instance (not reinitialize)
        result2 = await registry.get_or_create("test_service")
        assert result2 is result1
        assert call_count == 1  # Should still be 1

    @pytest.mark.asyncio
    async def test_concurrent_initialization(self):
        """Test that concurrent requests only initialize once."""
        registry = LazyServiceRegistry()
        init_count = 0

        async def slow_init():
            nonlocal init_count
            init_count += 1
            await asyncio.sleep(0.1)  # Simulate slow init
            return {"count": init_count}

        registry.register_factory("slow_service", slow_init)

        # Start multiple concurrent gets
        results = await asyncio.gather(
            registry.get_or_create("slow_service"),
            registry.get_or_create("slow_service"),
            registry.get_or_create("slow_service"),
        )

        # Should only initialize once
        assert init_count == 1
        # All should get same instance
        assert results[0] is results[1]
        assert results[1] is results[2]

    @pytest.mark.asyncio
    async def test_unregistered_service(self):
        """Test that requesting unregistered service raises KeyError."""
        registry = LazyServiceRegistry()

        with pytest.raises(KeyError):
            await registry.get_or_create("nonexistent")

    @pytest.mark.asyncio
    async def test_shutdown_all(self):
        """Test that shutdown_all calls shutdown on services."""
        registry = LazyServiceRegistry()

        class MockService:
            def __init__(self):
                self.shutdown_called = False

            async def shutdown(self):
                self.shutdown_called = True

        async def create_mock():
            return MockService()

        registry.register_factory("mock", create_mock)

        # Initialize the service
        service = await registry.get_or_create("mock")
        assert not service.shutdown_called

        # Shutdown all
        await registry.shutdown_all()
        assert service.shutdown_called
        assert not registry.is_initialized("mock")

    def test_get_initialized_services(self):
        """Test getting status of all services."""
        registry = LazyServiceRegistry()

        async def create_dummy():
            return {}

        registry.register_factory("service1", create_dummy)
        registry.register_factory("service2", create_dummy)

        status = registry.get_initialized_services()
        assert "service1" in status
        assert "service2" in status
        assert status["service1"] is False
        assert status["service2"] is False

    @pytest.mark.asyncio
    async def test_global_registry_singleton(self):
        """Test that get_lazy_registry returns singleton."""
        registry1 = get_lazy_registry()
        registry2 = get_lazy_registry()

        assert registry1 is registry2

        # Reset and get new one
        reset_lazy_registry()
        registry3 = get_lazy_registry()
        assert registry3 is not registry1


# === Test Resource Analyzer ===


class TestResourceAnalyzer:
    """Test resource analyzer for plugin dependencies."""

    def test_resource_types_enum(self):
        """Test that ResourceType enum has expected values."""
        assert ResourceType.LLM == "llm"
        assert ResourceType.POSTGRES == "postgres"
        assert ResourceType.VECTORSTORE == "vectorstore"
        assert ResourceType.GRAPH == "graph"
        assert ResourceType.REDIS == "redis"

    def test_init_order_simple(self):
        """Test initialization order with simple deps."""
        analyzer = ResourceAnalyzer(Path("plugins"))

        resources = {"postgres", "llm"}
        order = analyzer.get_resource_init_order(resources)

        # Both have no deps, order doesn't matter but should contain both
        assert len(order) == 2
        assert "postgres" in order
        assert "llm" in order

    def test_init_order_with_dependencies(self):
        """Test initialization order respects dependencies."""
        analyzer = ResourceAnalyzer(Path("plugins"))

        resources = {"postgres", "vectorstore", "memory"}
        order = analyzer.get_resource_init_order(resources)

        # Memory depends on vectorstore, vectorstore depends on postgres
        # So postgres should come before vectorstore, vectorstore before memory
        assert order.index("postgres") < order.index("vectorstore")
        assert order.index("vectorstore") < order.index("memory")

    def test_init_order_complex(self):
        """Test initialization order with complex dependencies."""
        analyzer = ResourceAnalyzer(Path("plugins"))

        resources = {
            "redis",
            "postgres",
            "graph",
            "vectorstore",
            "memory",
            "evaluation",
        }
        order = analyzer.get_resource_init_order(resources)

        # Verify dependency order
        assert order.index("redis") < order.index("graph")  # graph needs redis
        assert order.index("postgres") < order.index("vectorstore")
        assert order.index("vectorstore") < order.index("memory")
        assert order.index("redis") < order.index("memory")
        assert order.index("memory") < order.index("evaluation")

    def test_analyze_requirements_no_plugins(self):
        """Test analyzing with no plugins enabled."""
        analyzer = ResourceAnalyzer(Path("plugins"))

        requirements = analyzer.analyze_requirements({})

        assert requirements["required"] == set()
        assert requirements["optional"] == set()

    def test_analyze_requirements_disabled_plugin(self):
        """Test that disabled plugins are ignored."""
        analyzer = ResourceAnalyzer(Path("plugins"))

        requirements = analyzer.analyze_requirements(
            {"test_plugin": {"enabled": False}}
        )

        assert requirements["required"] == set()
        assert requirements["optional"] == set()


# === Test Plugin Metadata ===


class TestPluginMetadata:
    """Test PluginMetadata with resource requirements."""

    def test_metadata_with_resources(self):
        """Test creating metadata with resource requirements."""
        metadata = PluginMetadata(
            name="test_plugin",
            version="1.0.0",
            description="Test plugin",
            author="Test",
            required_resources=["postgres", "llm"],
            optional_resources=["graph"],
        )

        assert metadata.name == "test_plugin"
        assert metadata.required_resources == ["postgres", "llm"]
        assert metadata.optional_resources == ["graph"]

    def test_metadata_without_resources(self):
        """Test creating metadata without resource requirements (backward compat)."""
        metadata = PluginMetadata(
            name="test_plugin", version="1.0.0", description="Test plugin"
        )

        assert metadata.required_resources == []
        assert metadata.optional_resources == []

    def test_metadata_to_dict(self):
        """Test converting metadata to dictionary."""
        metadata = PluginMetadata(
            name="test_plugin",
            version="1.0.0",
            description="Test",
            required_resources=["postgres"],
            optional_resources=["llm"],
        )

        data = metadata.to_dict()

        assert data["name"] == "test_plugin"
        assert data["version"] == "1.0.0"
        assert data["required_resources"] == ["postgres"]
        assert data["optional_resources"] == ["llm"]


# === Integration Tests ===


@pytest.mark.integration
class TestLazyLoadingIntegration:
    """Integration tests for lazy loading system."""

    @pytest.mark.asyncio
    async def test_services_not_initialized_without_plugins(self):
        """Test that services aren't initialized when no plugins need them."""
        reset_lazy_registry()
        registry = get_lazy_registry()

        # Register factories but don't initialize
        async def create_llm():
            return {"type": "llm"}

        async def create_db():
            return {"type": "db"}

        registry.register_factory("llm", create_llm)
        registry.register_factory("postgres", create_db)

        # Nothing should be initialized
        status = registry.get_initialized_services()
        assert all(not initialized for initialized in status.values())

    @pytest.mark.asyncio
    async def test_only_required_services_initialized(self):
        """Test that only required services are initialized."""
        reset_lazy_registry()
        registry = get_lazy_registry()

        init_log = []

        async def create_llm():
            init_log.append("llm")
            return {"type": "llm"}

        async def create_db():
            init_log.append("postgres")
            return {"type": "postgres"}

        async def create_graph():
            init_log.append("graph")
            return {"type": "graph"}

        registry.register_factory("llm", create_llm)
        registry.register_factory("postgres", create_db)
        registry.register_factory("graph", create_graph)

        # Only initialize postgres
        await registry.get_or_create("postgres")

        # Only postgres should be initialized
        assert "postgres" in init_log
        assert "llm" not in init_log
        assert "graph" not in init_log
        assert registry.is_initialized("postgres")
        assert not registry.is_initialized("llm")
        assert not registry.is_initialized("graph")
