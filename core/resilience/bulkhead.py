"""
Bulkhead pattern.

Limits concurrent execution of specific operations to prevent resource exhaustion.
"""

import asyncio
import functools
from threading import Lock
from typing import Any, Callable, TypeVar, Optional

from core.config.resilience import get_resilience_config

F = TypeVar("F", bound=Callable[..., Any])


class Bulkhead:
    """
    Bulkhead pattern for limiting concurrent operations.

    Prevents resource exhaustion by limiting how many operations
    can run concurrently.

    Example:
        ```python
        api_bulkhead = Bulkhead(max_concurrent=10)

        @api_bulkhead
        async def call_api():
            return await make_request()
        ```
    """

    def __init__(self, max_concurrent: Optional[int] = None, name: str = "default"):
        """
        Initialize bulkhead.

        Args:
            max_concurrent: Maximum concurrent operations (default: from config)
            name: Name for logging
        """
        config = get_resilience_config()
        self.max_concurrent = (
            max_concurrent
            if max_concurrent is not None
            else config.bulkhead_max_concurrent
        )
        self.name = name
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._current = 0
        self._lock = Lock()

    @property
    def available(self) -> int:
        """Get number of available slots."""
        with self._lock:
            return self.max_concurrent - self._current

    def __call__(self, func: F) -> F:
        """Decorator to wrap async function with bulkhead."""
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("Bulkhead decorator only works with async functions")

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            async with self._semaphore:
                with self._lock:
                    self._current += 1
                try:
                    return await func(*args, **kwargs)
                finally:
                    with self._lock:
                        self._current -= 1

        return wrapper  # type: ignore
