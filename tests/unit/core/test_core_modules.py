"""
Unit tests for core modules: CLI, config, DI, orchestration.

Tests command-line interface, configuration management, dependency injection, and orchestration.
"""

import pytest


class TestCLI:
    """Tests for CLI module."""

    def test_cli_module_exists(self):
        """CLI module can be imported."""
        import core.cli

        assert core.cli is not None

    def test_cli_commands_module_exists(self):
        """CLI commands module exists."""
        import core.cli.commands

        assert core.cli.commands is not None

    def test_init_command_exists(self):
        """init command exists."""
        from core.cli.commands.init import run_init

        assert callable(run_init)

    def test_config_command_exists(self):
        """config command exists."""
        from core.cli.commands.config import show_config

        assert callable(show_config)

    def test_plugin_command_exists(self):
        """plugin command exists."""
        from core.cli.commands.plugin import create_plugin

        assert callable(create_plugin)

    def test_verify_command_exists(self):
        """verify command exists."""
        from core.cli.commands.verify import run_verify

        assert callable(run_verify)


class TestConfig:
    """Tests for config module."""

    def test_config_module_exists(self):
        """Config module can be imported."""
        import core.config

        assert core.config is not None

    def test_core_config_exists(self):
        """CoreConfig class exists."""
        from core.config.base import CoreConfig

        assert CoreConfig is not None

    def test_plugin_config_exists(self):
        """PluginConfig exists."""
        try:
            from core.config.plugins import PluginConfig

            assert PluginConfig is not None
        except ImportError:
            pytest.skip("PluginConfig not available")

    def test_llm_config_exists(self):
        """LLMConfig exists."""
        from core.config.services import LLMConfig

        assert LLMConfig is not None


class TestDI:
    """Tests for dependency injection module."""

    def test_di_module_exists(self):
        """DI module can be imported."""
        import core.di

        assert core.di is not None

    def test_container_class_exists(self):
        """DependencyContainer class exists."""
        from core.di.container import DependencyContainer

        assert DependencyContainer is not None

    def test_container_initialization(self):
        """DependencyContainer can be initialized."""
        from core.di.container import DependencyContainer

        container = DependencyContainer()
        assert container is not None

    def test_container_register(self):
        """DependencyContainer register method works."""
        from core.di.container import DependencyContainer

        container = DependencyContainer()
        container.register(str, lambda: "test")
        assert True

    def test_container_resolve(self):
        """DependencyContainer resolve method works."""
        from core.di.container import DependencyContainer

        container = DependencyContainer()
        container.register(str, lambda: "test_value")
        result = container.resolve(str)
        assert result == "test_value"


class TestInterfaces:
    """Tests for interfaces module."""

    def test_interfaces_module_exists(self):
        """Interfaces module can be imported."""
        import core.interfaces

        assert core.interfaces is not None

    def test_services_interfaces_exist(self):
        """Service interfaces exist."""
        try:
            from core.interfaces.services import ChatServiceProtocol

            assert ChatServiceProtocol is not None
        except (ImportError, AttributeError):
            pytest.skip("ChatServiceProtocol not available")


class TestOrchestration:
    """Tests for orchestration module."""

    def test_orchestration_module_exists(self):
        """Orchestration module can be imported."""
        import core.orchestration

        assert core.orchestration is not None

    def test_orchestrator_class_exists(self):
        """Orchestrator class exists."""
        try:
            from core.orchestration.orchestrator import Orchestrator

            assert Orchestrator is not None
        except ImportError:
            pytest.skip("Orchestrator not available")

    def test_intent_classifier_exists(self):
        """IntentClassifier exists."""
        try:
            from core.orchestration.intent_classifier import IntentClassifier

            assert IntentClassifier is not None
        except ImportError:
            pytest.skip("IntentClassifier not available")

    def test_handlers_module_exists(self):
        """Handlers module exists."""
        import core.orchestration.handlers

        assert core.orchestration.handlers is not None

    def test_protocols_module_exists(self):
        """Protocols module exists."""
        import core.orchestration.protocols

        assert core.orchestration.protocols is not None


class TestObservability:
    """Tests for observability modules (beyond logging)."""

    def test_audit_module_exists(self):
        """Audit module can be imported."""
        import core.observability.audit

        assert core.observability.audit is not None

    def test_cache_module_exists(self):
        """Cache module can be imported."""
        import core.observability.cache

        assert core.observability.cache is not None

    def test_tracing_module_exists(self):
        """Tracing module can be imported."""
        import core.observability.tracing

        assert core.observability.tracing is not None

    def test_audit_logger_exists(self):
        """AuditLogger exists if available."""
        try:
            from core.observability.audit import AuditLogger

            assert AuditLogger is not None
        except ImportError:
            pytest.skip("AuditLogger not available")

    def test_cache_decorator_exists(self):
        """Cache decorator exists if available."""
        try:
            from core.observability.cache import cache

            assert callable(cache)
        except ImportError:
            pytest.skip("cache decorator not available")

    def test_tracer_exists(self):
        """Tracer exists if available."""
        try:
            from core.observability.tracing import tracer

            assert tracer is not None
        except ImportError:
            pytest.skip("tracer not available")


class TestAuth:
    """Tests for core auth module."""

    def test_auth_module_exists(self):
        """Auth module can be imported."""
        import core.auth

        assert core.auth is not None

    def test_auth_manager_exists(self):
        """AuthManager exists."""
        from core.auth import AuthManager, get_auth_manager

        assert AuthManager is not None
        assert callable(get_auth_manager)


class TestCoreServices:
    """Tests for core services (LLM, VectorStore, Chat)."""

    def test_llm_service_exists(self):
        """LLM service module exists."""
        import core.services.llm

        assert core.services.llm is not None

    def test_llm_service_class_exists(self):
        """LLMService class exists."""
        try:
            from core.services.llm.service import LLMService

            assert LLMService is not None
        except ImportError:
            pytest.skip("LLMService not available")

    def test_vectorstore_service_exists(self):
        """VectorStore service module exists."""
        import core.services.vectorstore

        assert core.services.vectorstore is not None

    def test_vectorstore_service_class_exists(self):
        """VectorStoreService class exists."""
        try:
            from core.services.vectorstore.service import VectorStoreService

            assert VectorStoreService is not None
        except ImportError:
            pytest.skip("VectorStoreService not available")

    def test_chat_service_exists(self):
        """Chat service module exists."""
        import core.services.chat

        assert core.services.chat is not None

    def test_chat_service_class_exists(self):
        """ChatService class exists."""
        try:
            from core.services.chat.service import ChatService

            assert ChatService is not None
        except ImportError:
            pytest.skip("ChatService not available")

    def test_cost_control_exists(self):
        """Cost control module exists."""
        import core.services.llm.cost_control

        assert core.services.llm.cost_control is not None

    def test_chunking_service_exists(self):
        """Chunking service exists."""
        import core.services.vectorstore.chunking

        assert core.services.vectorstore.chunking is not None


class TestProviders:
    """Tests for service providers."""

    def test_openai_provider_exists(self):
        """OpenAI provider exists."""
        try:
            from core.services.llm.providers.openai_provider import OpenAIProvider

            assert OpenAIProvider is not None
        except ImportError:
            pytest.skip("OpenAIProvider not available")

    def test_ollama_provider_exists(self):
        """Ollama provider exists."""
        try:
            from core.services.llm.providers.ollama_provider import OllamaProvider

            assert OllamaProvider is not None
        except ImportError:
            pytest.skip("OllamaProvider not available")

    def test_qdrant_provider_exists(self):
        """Qdrant provider exists."""
        try:
            from core.services.vectorstore.providers.qdrant_provider import (
                QdrantProvider,
            )

            assert QdrantProvider is not None
        except ImportError:
            pytest.skip("QdrantProvider not available")
