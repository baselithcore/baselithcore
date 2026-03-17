"""
Storage configuration.

Database, GraphDB, and Redis settings.
"""

import logging
from urllib.parse import quote_plus, urlencode
from typing import Optional

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# NOTE: Using direct logging.getLogger() here instead of core.observability.logging.get_logger()
# This is intentional: config modules initialize during framework bootstrap, before the
# observability infrastructure is fully set up. Direct logging prevents circular dependencies.
logger = logging.getLogger(__name__)


class StorageConfig(BaseSettings):
    """
    Storage configuration.

    Handles PostgreSQL, GraphDB (RedisGraph), and Cache Redis settings.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === PostgreSQL ===
    database_url: Optional[str] = Field(
        default=None, description="Full database connection URL"
    )
    db_host: str = Field(default="postgres", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_name: str = Field(default="baselith", alias="DB_NAME")
    db_user: str = Field(default="baselith", alias="DB_USER")
    db_password: str = Field(default="baselith", alias="DB_PASSWORD")
    db_ssl_mode: Optional[str] = Field(default=None, alias="DB_SSL_MODE")

    db_pool_min_size: int = Field(default=1, alias="DB_POOL_MIN_SIZE", ge=1)
    db_pool_max_size: int = Field(default=20, alias="DB_POOL_MAX_SIZE", ge=1)
    db_pool_timeout: float = Field(default=30.0, alias="DB_POOL_TIMEOUT", ge=0.1)
    postgres_enabled: bool = Field(default=True, alias="POSTGRES_ENABLED")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def conninfo(self) -> str:
        """Build PostgreSQL connection info string."""
        if self.database_url:
            return self.database_url

        user = quote_plus(self.db_user or "")
        password = quote_plus(self.db_password) if self.db_password else ""
        password_fragment = f":{password}" if password else ""
        host = self.db_host or "localhost"
        port = self.db_port or 5432

        query_params = {}
        if self.db_ssl_mode:
            query_params["sslmode"] = self.db_ssl_mode
        query = f"?{urlencode(query_params)}" if query_params else ""

        return f"postgresql://{user}{password_fragment}@{host}:{port}/{self.db_name}{query}"

    # === GraphDB ===
    graph_db_enabled: bool = Field(default=True, alias="GRAPH_DB_ENABLED")
    graph_db_url: str = Field(default="redis://localhost:6379", alias="GRAPH_DB_URL")
    graph_db_name: str = Field(default="agent_graph", alias="GRAPH_DB_NAME")
    graph_db_timeout: float = Field(default=2.0, alias="GRAPH_DB_TIMEOUT", ge=0.1)
    graph_similar_top_k: int = Field(default=5, alias="GRAPH_SIMILAR_TOP_K", ge=1)
    graph_rag_enabled: bool = Field(default=False, alias="GRAPH_RAG_ENABLED")
    graph_cache_ttl: int = Field(default=3600, alias="GRAPH_CACHE_TTL")

    # === Cache Backend ===
    # For now, simplistic cache config. Ideally dedicated CacheConfig.
    cache_backend: str = Field(default="local", alias="CACHE_BACKEND")
    cache_redis_url: str = Field(
        default="redis://localhost:6379/1", alias="CACHE_REDIS_URL"
    )
    cache_redis_prefix: str = Field(default="baselithcore", alias="CACHE_REDIS_PREFIX")

    # === Task Queue ===
    queue_redis_url: str = Field(
        default="redis://localhost:6379/2", alias="QUEUE_REDIS_URL"
    )

    # Cost control / Performance related to usage
    graph_query_limit: int = Field(default=30, alias="GRAPH_QUERY_LIMIT", ge=1)
    graph_max_hops: int = Field(default=3, alias="GRAPH_MAX_HOPS", ge=1)
    graph_query_timeout: float = Field(default=5.0, alias="GRAPH_QUERY_TIMEOUT", ge=0.1)


# Global instance
_storage_config: Optional[StorageConfig] = None


def get_storage_config() -> StorageConfig:
    """Get or create the global storage configuration instance."""
    global _storage_config
    if _storage_config is None:
        _storage_config = StorageConfig()
        logger.info(
            f"Initialized StorageConfig with pg_enabled={_storage_config.postgres_enabled}, graph_enabled={_storage_config.graph_db_enabled}"
        )
    return _storage_config
