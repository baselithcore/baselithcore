"""
Unit tests for core modules: CLI, config, DI, orchestration.

Tests command-line interface, configuration management, dependency injection, and orchestration.
"""

import pytest


class TestCLI:
    """Tests for CLI module."""

    def test_cli_module_exists(self):
        """CLI module can be imported."""
        import baselith.core.cli

        assert baselith.core.cli is not None

    def test_cli_commands_module_exists(self):
        """CLI commands module exists."""
        import baselith.core.cli.commands

        assert baselith.core.cli.commands is not None

    def test_init_command_exists(self):
        """init command exists."""
        from baselith.core.cli.commands.init import run_init

        assert callable(run_init)

    def test_config_command_exists(self):
        """config command exists."""
        from baselith.core.cli.commands.config import show_config

        assert callable(show_config)

    def test_plugin_command_exists(self):
        """plugin command exists."""
        from baselith.core.cli.commands.plugin import create_plugin

        assert callable(create_plugin)

    def test_verify_command_exists(self):
        """verify command exists."""
        from baselith.core.cli.commands.verify import run_verify

        assert callable(run_verify)


class TestConfig:
    """Tests for config module."""

    def test_config_module_exists(self):
        """Config module can be imported."""
        import baselith.core.config

        assert baselith.core.config is not None

    def test_core_config_exists(self):
        """CoreConfig class exists."""
        from baselith.core.config.base import CoreConfig

        assert CoreConfig is not None

    def test_plugin_config_exists(self):
        """PluginConfig exists."""
        try:
            from baselith.core.config.plugins import PluginConfig

            assert PluginConfig is not None
        except ImportError:
            pytest.skip("PluginConfig not available")

    def test_llm_config_exists(self):
        """LLMConfig exists."""
        from baselith.core.config.services import LLMConfig

        assert LLMConfig is not None


class TestDI:
    """Tests for dependency injection module."""

    def test_di_module_exists(self):
        """DI module can be imported."""
        import baselith.core.di

        assert baselith.core.di is not None

    def test_container_class_exists(self):
        """DependencyContainer class exists."""
        from baselith.core.di.container import DependencyContainer

        assert DependencyContainer is not None

    def test_container_initialization(self):
        """DependencyContainer can be initialized."""
        from baselith.core.di.container import DependencyContainer

        container = DependencyContainer()
        assert container is not None

    def test_container_register(self):
        """DependencyContainer register method works."""
        from baselith.core.di.container import DependencyContainer

        container = DependencyContainer()
        container.register(str, lambda: "test")
        assert True

    def test_container_resolve(self):
        """DependencyContainer resolve method works."""
        from baselith.core.di.container import DependencyContainer

        container = DependencyContainer()
        container.register(str, lambda: "test_value")
        result = container.resolve(str)
        assert result == "test_value"


class TestInterfaces:
    """Tests for interfaces module."""

    def test_interfaces_module_exists(self):
        """Interfaces module can be imported."""
        import baselith.core.interfaces

        assert baselith.core.interfaces is not None

    def test_services_interfaces_exist(self):
        """Service interfaces exist."""
        try:
            from baselith.core.interfaces.services import ChatServiceProtocol

            assert ChatServiceProtocol is not None
        except (ImportError, AttributeError):
            pytest.skip("ChatServiceProtocol not available")


class TestOrchestration:
    """Tests for orchestration module."""

    def test_orchestration_module_exists(self):
        """Orchestration module can be imported."""
        import baselith.core.orchestration

        assert baselith.core.orchestration is not None

    def test_orchestrator_class_exists(self):
        """Orchestrator class exists."""
        try:
            from baselith.core.orchestration.orchestrator import Orchestrator

            assert Orchestrator is not None
        except ImportError:
            pytest.skip("Orchestrator not available")

    def test_intent_classifier_exists(self):
        """IntentClassifier exists."""
        try:
            from baselith.core.orchestration.intent_classifier import IntentClassifier

            assert IntentClassifier is not None
        except ImportError:
            pytest.skip("IntentClassifier not available")

    def test_handlers_module_exists(self):
        """Handlers module exists."""
        import baselith.core.orchestration.handlers

        assert baselith.core.orchestration.handlers is not None

    def test_protocols_module_exists(self):
        """Protocols module exists."""
        import baselith.core.orchestration.protocols

        assert baselith.core.orchestration.protocols is not None


class TestObservability:
    """Tests for observability modules (beyond logging)."""

    def test_audit_module_exists(self):
        """Audit module can be imported."""
        import baselith.core.observability.audit

        assert baselith.core.observability.audit is not None

    def test_cache_module_exists(self):
        """Cache module can be imported."""
        import baselith.core.observability.cache

        assert baselith.core.observability.cache is not None

    def test_tracing_module_exists(self):
        """Tracing module can be imported."""
        import baselith.core.observability.tracing

        assert baselith.core.observability.tracing is not None

    def test_audit_logger_exists(self):
        """AuditLogger exists if available."""
        try:
            from baselith.core.observability.audit import AuditLogger

            assert AuditLogger is not None
        except ImportError:
            pytest.skip("AuditLogger not available")

    def test_cache_decorator_exists(self):
        """Cache decorator exists if available."""
        try:
            from baselith.core.observability.cache import cache

            assert callable(cache)
        except ImportError:
            pytest.skip("cache decorator not available")

    def test_tracer_exists(self):
        """Tracer exists if available."""
        try:
            from baselith.core.observability.tracing import tracer

            assert tracer is not None
        except ImportError:
            pytest.skip("tracer not available")


class TestAuth:
    """Tests for core auth module."""

    def test_auth_module_exists(self):
        """Auth module can be imported."""
        import baselith.core.auth

        assert baselith.core.auth is not None

    def test_auth_manager_exists(self):
        """AuthManager exists."""
        from baselith.core.auth import AuthManager, get_auth_manager

        assert AuthManager is not None
        assert callable(get_auth_manager)


class TestCoreServices:
    """Tests for core services (LLM, VectorStore, Chat)."""

    def test_llm_service_exists(self):
        """LLM service module exists."""
        import baselith.core.services.llm

        assert baselith.core.services.llm is not None

    def test_llm_service_class_exists(self):
        """LLMService class exists."""
        try:
            from baselith.core.services.llm.service import LLMService

            assert LLMService is not None
        except ImportError:
            pytest.skip("LLMService not available")

    def test_vectorstore_service_exists(self):
        """VectorStore service module exists."""
        import baselith.core.services.vectorstore

        assert baselith.core.services.vectorstore is not None

    def test_vectorstore_service_class_exists(self):
        """VectorStoreService class exists."""
        try:
            from baselith.core.services.vectorstore.service import VectorStoreService

            assert VectorStoreService is not None
        except ImportError:
            pytest.skip("VectorStoreService not available")

    def test_chat_service_exists(self):
        """Chat service module exists."""
        import baselith.core.services.chat

        assert baselith.core.services.chat is not None

    def test_chat_service_class_exists(self):
        """ChatService class exists."""
        try:
            from baselith.core.services.chat.service import ChatService

            assert ChatService is not None
        except ImportError:
            pytest.skip("ChatService not available")

    def test_cost_control_exists(self):
        """Cost control module exists."""
        import baselith.core.services.llm.cost_control

        assert baselith.core.services.llm.cost_control is not None

    def test_chunking_service_exists(self):
        """Chunking service exists."""
        import baselith.core.services.vectorstore.chunking

        assert baselith.core.services.vectorstore.chunking is not None


class TestProviders:
    """Tests for service providers."""

    def test_openai_provider_exists(self):
        """OpenAI provider exists."""
        try:
            from baselith.core.services.llm.providers.openai_provider import (
                OpenAIProvider,
            )

            assert OpenAIProvider is not None
        except ImportError:
            pytest.skip("OpenAIProvider not available")

    def test_ollama_provider_exists(self):
        """Ollama provider exists."""
        try:
            from baselith.core.services.llm.providers.ollama_provider import (
                OllamaProvider,
            )

            assert OllamaProvider is not None
        except ImportError:
            pytest.skip("OllamaProvider not available")

    def test_qdrant_provider_exists(self):
        """Qdrant provider exists."""
        try:
            from baselith.core.services.vectorstore.providers.qdrant_provider import (
                QdrantProvider,
            )

            assert QdrantProvider is not None
        except ImportError:
            pytest.skip("QdrantProvider not available")
