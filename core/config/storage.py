"""
Storage configuration.

Database, GraphDB, and Redis settings.
"""

import logging
from urllib.parse import quote_plus, urlencode, urlsplit

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.config.environment import is_production_env

# NOTE: Using direct logging.getLogger() here instead of core.observability.logging.get_logger()
# This is intentional: config modules initialize during framework bootstrap, before the
# observability infrastructure is fully set up. Direct logging prevents circular dependencies.
logger = logging.getLogger(__name__)


def _redis_url_is_unauthenticated(url: str) -> bool:
    """True for a plaintext ``redis://`` URL with no password (not ``rediss://``)."""
    try:
        parts = urlsplit(url)
    except Exception:
        return False
    if parts.scheme != "redis":
        return False  # rediss:// (TLS) or other schemes are considered fine here
    return not parts.password


class StorageConfig(BaseSettings):
    """
    Storage configuration.

    Handles PostgreSQL, GraphDB (RedisGraph), and Cache Redis settings.
    """

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    # === PostgreSQL ===
    database_url: str | None = Field(
        default=None, description="Full database connection URL"
    )
    db_host: str = Field(default="postgres", alias="DB_HOST")
    db_port: int = Field(default=5432, alias="DB_PORT")
    db_name: str = Field(default="baselith", alias="DB_NAME")
    db_user: str = Field(default="baselith", alias="DB_USER")
    db_password: SecretStr = Field(default=SecretStr(""), alias="DB_PASSWORD")
    db_ssl_mode: str | None = Field(default=None, alias="DB_SSL_MODE")
    # Optional read replica. When set, callers using the read-only connection
    # API are routed here; unset means reads use the primary (no behaviour change).
    db_replica_url: str | None = Field(default=None, alias="DB_REPLICA_URL")

    @model_validator(mode="after")
    def _require_db_password_in_production(self) -> "StorageConfig":
        if is_production_env() and self.postgres_enabled:
            if not self.database_url and not self.db_password.get_secret_value():
                raise ValueError(
                    "DB_PASSWORD must be set when production mode is enabled and POSTGRES_ENABLED=true"
                )
        return self

    @model_validator(mode="after")
    def _warn_insecure_transport_in_production(self) -> "StorageConfig":
        """Warn (do not fail) when data-tier transport is unencrypted in prod.

        Credentials and PII transit these connections; TLS should be on. Kept
        as a warning rather than a hard error so it never breaks an existing
        deployment on upgrade — operators get an actionable signal to enable
        ``sslmode=require`` (Postgres) and ``rediss://``/AUTH (Redis).
        """
        if not is_production_env():
            return self

        # PostgreSQL: sslmode unset or in a non-encrypting mode.
        if self.postgres_enabled and not self.database_url:
            weak_ssl = (self.db_ssl_mode or "").lower() in (
                "",
                "disable",
                "allow",
                "prefer",
            )
            if weak_ssl:
                logger.warning(
                    "Postgres TLS not enforced in production (DB_SSL_MODE=%r). "
                    "Set DB_SSL_MODE=require (or verify-full) so credentials/PII "
                    "are not sent in plaintext.",
                    self.db_ssl_mode,
                )

        # Redis roles: plaintext scheme without embedded auth.
        for label, url in (
            ("GRAPH_DB_URL", self.graph_db_url),
            ("CACHE_REDIS_URL", self.cache_redis_url),
            ("QUEUE_REDIS_URL", self.queue_redis_url),
        ):
            if _redis_url_is_unauthenticated(url):
                logger.warning(
                    "%s uses an unauthenticated, non-TLS Redis connection in "
                    "production. Use rediss:// with AUTH/ACL so cache, graph, and "
                    "queue data (and pickled jobs) are not exposed to any host "
                    "that can reach Redis.",
                    label,
                )
        return self

    db_pool_min_size: int = Field(default=1, alias="DB_POOL_MIN_SIZE", ge=1)
    db_pool_max_size: int = Field(default=20, alias="DB_POOL_MAX_SIZE", ge=1)
    db_pool_timeout: float = Field(default=30.0, alias="DB_POOL_TIMEOUT", ge=0.1)
    postgres_enabled: bool = Field(default=True, alias="POSTGRES_ENABLED")
    # Row-Level-Security defense-in-depth. When True, every pooled connection
    # has the `app.tenant_id` GUC set to the request's tenant on checkout, so
    # tables with RLS policies (USING tenant_id = current_setting('app.tenant_id'))
    # are isolated at the database. OFF by default: enabling it has no effect
    # until RLS policies exist AND the app connects as a non-owner (or FORCE RLS)
    # role — so toggling the flag alone is a no-op and never a regression.
    db_rls_enabled: bool = Field(default=False, alias="DB_RLS_ENABLED")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def conninfo(self) -> str:
        """Build PostgreSQL connection info string."""
        if self.database_url:
            return self.database_url

        user = quote_plus(self.db_user or "")
        _pw = self.db_password.get_secret_value() if self.db_password else ""
        password = quote_plus(_pw) if _pw else ""
        password_fragment = f":{password}" if password else ""
        host = self.db_host or "localhost"
        port = self.db_port or 5432

        query_params = {}
        if self.db_ssl_mode:
            query_params["sslmode"] = self.db_ssl_mode
        query = f"?{urlencode(query_params)}" if query_params else ""

        return f"postgresql://{user}{password_fragment}@{host}:{port}/{self.db_name}{query}"

    @property
    def replica_conninfo(self) -> str | None:
        """Read-replica connection string, or ``None`` if no replica is set."""
        return self.db_replica_url or None

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
    # Per-request cap on *relational* (Postgres) queries. Distinct from the graph
    # (Cypher) budget above: a single agentic HTTP request legitimately issues
    # hundreds of SQL statements (tool reads + the final transcript write), so
    # the tight graph limit must never gate SQL. 0 = unlimited (default) — set a
    # positive value only to guard against pathological query fan-out.
    sql_query_limit: int = Field(default=0, alias="SQL_QUERY_LIMIT", ge=0)


# Global instance
_storage_config: StorageConfig | None = None


def get_storage_config() -> StorageConfig:
    """Get or create the global storage configuration instance."""
    global _storage_config
    if _storage_config is None:
        _storage_config = StorageConfig()
        logger.info(
            f"Initialized StorageConfig with pg_enabled={_storage_config.postgres_enabled}, graph_enabled={_storage_config.graph_db_enabled}"
        )
    return _storage_config
