import os
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class TaskQueueConfig(BaseSettings):
    """Configuration for task queue system."""

    model_config = SettingsConfigDict(
        env_prefix="",  # Read from both TASK_QUEUE_ and QUEUE_ prefixes
        case_sensitive=False,
        env_file=".env",
        extra="ignore",
    )

    redis_url: Optional[str] = None
    queue_redis_url: Optional[str] = None  # Alternative env var name

    def get_redis_url(self) -> str:
        """Get Redis URL with fallback logic."""
        # Try multiple environment variable names
        url = (
            self.redis_url
            or self.queue_redis_url
            or os.getenv("QUEUE_REDIS_URL")
            or os.getenv("TASK_QUEUE_REDIS_URL")
            or "redis://falkordb:6379/2"  # Default for Docker
        )
        return url

    queues: List[str] = ["default", "documents", "analysis"]
    default_queue: str = "default"

    # Task execution settings
    job_timeout: int = 3600  # 1 hour
    result_ttl: int = 86400  # 24 hours
    failure_ttl: int = 604800  # 7 days

    # Retry settings
    default_retry_count: int = 3
    default_retry_delay: int = 60
