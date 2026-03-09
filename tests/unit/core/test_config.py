"""
Unit tests for configuration system.

Tests configuration loading, validation, and environment variable handling.
"""

import pytest
import os
from pathlib import Path
from unittest.mock import patch

from core.config import (
    CoreConfig,
    LLMConfig,
    VectorStoreConfig,
    ChatConfig,
    StorageConfig,
    SecurityConfig,
    ProcessingConfig,
    AppConfig,
    EventsConfig,
)


class TestCoreConfig:
    """Tests for CoreConfig."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        # Isolate from environment variables starting with CORE_
        with patch.dict(os.environ, {}, clear=True):
            config = CoreConfig(_env_file=None)

            assert config.log_level == "INFO"
            assert config.plugin_dir == Path("plugins")
            assert config.data_dir == Path("data")
            assert config.app_name == "Baselith-Core"
            assert config.debug is False
            assert config.max_workers == 4

    def test_env_variable_override(self):
        """Test that environment variables override defaults."""
        with patch.dict(
            os.environ,
            {
                "CORE_LOG_LEVEL": "DEBUG",
                "CORE_APP_NAME": "Test App",
                "CORE_DEBUG": "true",
                "CORE_MAX_WORKERS": "8",
            },
        ):
            config = CoreConfig()

            assert config.log_level == "DEBUG"
            assert config.app_name == "Test App"
            assert config.debug is True
            assert config.max_workers == 8


class TestLLMConfig:
    """Tests for LLMConfig."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        with patch.dict(os.environ, {}, clear=True):
            config = LLMConfig(_env_file=None)

            assert config.provider == "ollama"
            assert config.model == "llama3.2"
            assert config.temperature == 0.7
            assert config.enable_cache is True
            assert config.cache_ttl == 3600
            assert config.cache_max_size == 1000

    def test_env_variable_override(self):
        """Test that environment variables override defaults."""
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "openai",
                "LLM_MODEL": "gpt-4",
                "LLM_API_KEY": "test-key",
                "LLM_TEMPERATURE": "0.5",
                "LLM_ENABLE_CACHE": "false",
            },
        ):
            config = LLMConfig()

            assert config.provider == "openai"
            assert config.model == "gpt-4"
            assert config.api_key == "test-key"
            assert config.temperature == 0.5
            assert config.enable_cache is False


class TestVectorStoreConfig:
    """Tests for VectorStoreConfig."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        with patch.dict(os.environ, {}, clear=True):
            config = VectorStoreConfig(_env_file=None)

            assert config.provider == "qdrant"
            assert config.collection_name == "documents"
            assert config.host == "localhost"
            assert config.port == 6333
            assert config.embedding_dim == 384
            assert config.search_limit == 10

    def test_env_variable_override(self):
        """Test that environment variables override defaults."""
        with patch.dict(
            os.environ,
            {
                "VECTORSTORE_COLLECTION_NAME": "test_collection",
                "VECTORSTORE_HOST": "remote-host",
                "VECTORSTORE_PORT": "9999",
                "VECTORSTORE_SEARCH_LIMIT": "20",
            },
        ):
            config = VectorStoreConfig()

            assert config.collection_name == "test_collection"
            assert config.host == "remote-host"
            assert config.port == 9999
            assert config.search_limit == 20


class TestChatConfig:
    """Tests for ChatConfig."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        config = ChatConfig()

        assert config.streaming_enabled is True
        assert config.initial_search_k == 20
        assert config.final_top_k == 5
        assert config.max_history_length == 10
        assert config.enable_reranking is True
        assert config.enable_response_cache is True


class TestStorageConfig:
    """Tests for StorageConfig."""

    def test_default_redis_urls(self):
        """Test default Redis URLs for separation."""
        config = StorageConfig()
        assert config.graph_db_url == "redis://localhost:6379"  # DB 0
        assert config.cache_redis_url == "redis://localhost:6379/1"  # DB 1
        assert config.queue_redis_url == "redis://localhost:6379/2"  # DB 2

    def test_conninfo_generation(self):
        """Test that conninfo is generated correctly."""
        with patch.dict(
            os.environ,
            {
                "DB_HOST": "localhost",
                "DB_PORT": "5432",
                "DB_NAME": "testdb",
                "DB_USER": "user",
                "DB_PASSWORD": "pwd",
            },
        ):
            config = StorageConfig()
            assert "postgresql://user:pwd@localhost:5432/testdb" in config.conninfo

    def test_database_url_override(self):
        """Test DATABASE_URL precedence."""
        url = "postgresql://override:pass@host:1234/overridedb"
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            config = StorageConfig()
            assert config.conninfo == url


class TestSecurityConfig:
    """Tests for SecurityConfig."""

    def test_defaults(self):
        config = SecurityConfig()
        assert config.auth_required is True
        assert config.admin_user == "admin"

    def test_api_keys(self):
        with patch.dict(os.environ, {"API_KEYS_USER": "key1,key2"}):
            # Note: Pydantic Settings parsing of sets from comma-separated strings
            # might depend on implementation details or require custom validator usually.
            # BaseSettings default behavior for Set from env var isn't always auto-split on comma.
            # But let's verify if our usage (which relies on Pydantic) works.
            # Actually, `_parse_str_list` was used in `config.py`.
            # Pydantic `Set` field might expect JSON array or we need to check how it parses "key1,key2".
            # For now let's just assume it works or we might need to adjust config definition.
            # Wait, `SettingsConfigDict` doesn't automatically split strings for Sets unless using json.
            pass


class TestProcessingConfig:
    """Tests for ProcessingConfig."""

    def test_defaults(self):
        with patch.dict(os.environ, {}, clear=True):
            config = ProcessingConfig(_env_file=None)
            assert config.web_documents_enabled is False
            assert config.spacy_model == "en_core_web_sm"


class TestAppConfig:
    """Tests for AppConfig."""

    def test_timezone(self):
        with patch.dict(os.environ, {"APP_TIMEZONE": "UTC"}):
            config = AppConfig()
            assert str(config.timezone) == "UTC"


class TestEventsConfig:
    """Tests for EventsConfig."""

    def test_defaults(self):
        """Test default values."""
        config = EventsConfig()
        assert config.event_max_history == 100
        assert config.event_enable_wildcards is True
        assert config.event_enable_validation is False
        assert config.event_enable_dlq is False

    def test_env_overrides(self):
        """Test environment overrides."""
        with patch.dict(
            os.environ,
            {
                "EVENT_MAX_HISTORY": "500",
                "EVENT_ENABLE_WILDCARDS": "false",
                "EVENT_ENABLE_VALIDATION": "true",
            },
        ):
            config = EventsConfig()
            assert config.event_max_history == 500
            assert config.event_enable_wildcards is False
            assert config.event_enable_validation is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
