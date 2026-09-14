"""Task-queue configuration (``TASK_QUEUE_``).

Broker endpoint, worker concurrency and retry policy for deferred work.
"""

from typing import Annotated, Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.config._collections import csv_list


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
    # Both spellings are named explicitly. A single-string ``validation_alias``
    # accepts only the alias, so ``TaskQueueConfig(queue_redis_url=...)`` bound
    # nothing and silently fell through to the localhost default on the
    # pydantic-settings version ``uv.lock`` pins; newer releases happen to also
    # match the field name. Listing both removes the dependence on which
    # release is installed, and widens nothing: it is the same env var name,
    # and the generic names this alias exists to keep out stay out.
    queue_redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("QUEUE_REDIS_URL", "queue_redis_url"),
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

    # NoDecode + csv_list, same reason as dlq_replay_allowed_modules below:
    # `TASK_QUEUE_QUEUES=default,documents` must configure the queues, not
    # raise a SettingsError out of the whole task-queue configuration.
    queues: Annotated[list[str], NoDecode] = Field(
        default=["default", "documents", "analysis"]
    )
    default_queue: str = "default"

    # Task execution settings
    job_timeout: int = 3600  # 1 hour
    result_ttl: int = 86400  # 24 hours
    failure_ttl: int = 604800  # 7 days

    # Retry settings
    default_retry_count: int = 3
    default_retry_delay: int = 60

    # Dead-letter retention. The DLQ is a diagnosis and replay aid, not an
    # archive: without a horizon every terminally-failed job stays in Redis
    # forever, so one bad deploy's failures sit in memory indefinitely.
    # Matches ``failure_ttl`` so the two views of a failed job expire together.
    # 0 disables expiry (keep forever) — only for a deployment that prunes the
    # DLQ by hand.
    dlq_retention_seconds: int = 604800  # 7 days

    # Replay allowlist. A DLQ row is data read back out of Redis, and RQ
    # resolves whatever dotted path it is handed — so a row naming `os.system`
    # is an import away from running. Replay refuses any reference that does
    # not start with one of these module prefixes. Empty list = no replay at
    # all, which is the fail-closed reading of "nothing is allowed".
    # ``NoDecode``: without it pydantic-settings JSON-decodes complex fields in
    # ``EnvSettingsSource`` *before* any validator runs, so the documented
    # ``core.,plugins.`` raises SettingsError out of the whole class — taking
    # enqueue, the worker, the monitor and the CLI down with it, not just
    # replay. Same idiom as core/config/security.py.
    dlq_replay_allowed_modules: Annotated[list[str], NoDecode] = Field(
        default=["core.", "plugins."],
        description=(
            "Comma-separated module prefixes a dead-lettered job may be "
            "replayed from. A stored function reference outside them is "
            "refused before any import happens."
        ),
    )

    @field_validator("queues", "dlq_replay_allowed_modules", mode="before")
    @classmethod
    def _parse_csv_lists(cls, value: Any) -> Any:
        """Accept ``a,b`` and a blank value, as well as a JSON array.

        Paired with ``NoDecode`` on both fields — see
        :mod:`core.config._collections` for why both halves are needed.
        """
        return csv_list(value)

    # Connection pool settings
    max_connections: int = 50  # cap connections on the shared queue Redis pool
    health_check_interval: float = 30.0  # idle-connection health-check seconds
