"""
Task Queue Package.

Interfaces and conveniences for the asynchronous background job system.
Enables asynchronous and scheduled task execution using Redis and RQ.
"""

from typing import Optional, Any

from redis import Redis
from rq import Queue

from core.config import get_task_queue_config

# Global connection cache
_redis_conn: Optional[Redis] = None


def get_queue_redis_connection() -> Redis:
    """Get Redis connection for task queue."""
    global _redis_conn
    if _redis_conn is None:
        config = get_task_queue_config()
        # Default to localhost if not configured
        url = config.redis_url or "redis://localhost:6379/2"
        _redis_conn = Redis.from_url(url)
    return _redis_conn


def get_queue(name: str = "default") -> Queue:
    """Get a queue by name."""
    conn = get_queue_redis_connection()
    return Queue(name, connection=conn)


# Lazy import helper to avoid circular imports and allow proper initialization
def enqueue_task(*args, **kwargs):  # type: ignore[no-untyped-def]
    """
    Enqueue a task for immediate execution.
    Proxies to scheduler.enqueue_task wrapper.
    """
    from core.task_queue.scheduler import enqueue_task as _enqueue

    return _enqueue(*args, **kwargs)


def schedule_task(*args, **kwargs):  # type: ignore[no-untyped-def]
    """
    Schedule a task to run after a delay.
    Proxies to scheduler.schedule_task wrapper.
    """
    from core.task_queue.scheduler import schedule_task as _schedule

    return _schedule(*args, **kwargs)


# Backward compatibility - though discouraged to use directly
def __getattr__(name: str) -> Any:
    if name == "redis_conn":
        return get_queue_redis_connection()
    raise AttributeError(f"module {__name__} has no attribute {name}")


__all__ = [
    "get_queue_redis_connection",
    "get_queue",
    "enqueue_task",
    "schedule_task",
]
