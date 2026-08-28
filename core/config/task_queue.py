from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TaskQueueConfig(BaseSettings):
    """Configuration for task queue system.

    Environment variables are namespaced under ``TASK_QUEUE_`` (for example
    ``TASK_QUEUE_JOB_TIMEOUT``). The broker URL additionally accepts the
    documented, unprefixed ``QUEUE_REDIS_URL`` — the name used by
    ``StorageConfig.queue_redis_url`` and the shipped ``configs/.env.*`` files —
    so producers and consumers keep resolving the same database.
    """

    model_config = SettingsConfigDict(
        env_prefix="TASK_QUEUE_",
        case_sensitive=False,
        extra="ignore",
    )

    # TASK_QUEUE_REDIS_URL — the most specific name, so it wins.
    redis_url: str | None = None
    # QUEUE_REDIS_URL — the documented deployment name. Declared as an explicit
    # validation alias rather than relying on a bare field name: an empty
    # ``env_prefix`` would also bind generic names such as ``REDIS_URL`` or
    # ``MAX_CONNECTIONS``, letting an unrelated service silently redirect the
    # broker and strand every enqueued job on a database no worker listens on.
    queue_redis_url: str | None = Field(
        default=None, validation_alias="QUEUE_REDIS_URL"
    )

    def get_redis_url(self) -> str:
        """Get Redis URL with fallback logic."""
        return (
            self.redis_url
            or self.queue_redis_url
            # Matches StorageConfig.queue_redis_url and configs/.env.base so
            # producer and consumer agree when nothing is configured. Container
            # deployments set QUEUE_REDIS_URL explicitly (configs/.env.production).
            or "redis://localhost:6379/2"
        )

    queues: list[str] = ["default", "documents", "analysis"]
    default_queue: str = "default"

    # Task execution settings
    job_timeout: int = 3600  # 1 hour
    result_ttl: int = 86400  # 24 hours
    failure_ttl: int = 604800  # 7 days

    # Retry settings
    default_retry_count: int = 3
    default_retry_delay: int = 60

    # Connection pool settings
    max_connections: int = 50  # cap connections on the shared queue Redis pool
    health_check_interval: float = 30.0  # idle-connection health-check seconds
