"""
Retry and Timeout decorators.

Provides resilience patterns for:
- Retry with exponential backoff
- Timeout for async operations
"""

import asyncio
import functools
from core.observability.logging import get_logger
import random
import time
from typing import Any, Callable, TypeVar, Optional, Tuple, Type

from core.config.resilience import get_resilience_config

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class TimeoutError(Exception):
    """Raised when operation times out."""

    pass


def retry(
    max_attempts: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: Optional[float] = None,
    exponential_base: Optional[float] = None,
    jitter: Optional[bool] = None,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """
    Decorator for retry with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries
        exponential_base: Base for exponential backoff
        jitter: Add random jitter to prevent thundering herd
        retryable_exceptions: Tuple of exceptions to retry on

    Example:
        ```python
        @retry(max_attempts=3, base_delay=1.0)
        def flaky_api_call():
            return requests.get("https://api.example.com")
        ```
    """
    config = get_resilience_config()

    _max_attempts = (
        max_attempts if max_attempts is not None else config.retry_max_attempts
    )
    _base_delay = base_delay if base_delay is not None else config.retry_base_delay
    _max_delay = max_delay if max_delay is not None else config.retry_max_delay
    _exponential_base = (
        exponential_base
        if exponential_base is not None
        else config.retry_exponential_base
    )
    _jitter = jitter if jitter is not None else config.retry_jitter

    def decorator(func: F) -> F:
        """Apply retry logic to the function."""

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            """Sync wrapper for retry logic."""
            last_exception = None

            for attempt in range(1, _max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == _max_attempts:
                        logger.error(
                            f"Retry exhausted for {func.__name__} "
                            f"after {_max_attempts} attempts"
                        )
                        raise

                    delay = min(
                        _base_delay * (_exponential_base ** (attempt - 1)),
                        _max_delay,
                    )

                    if _jitter:
                        delay *= 0.5 + random.random()  # nosec B311

                    logger.warning(
                        f"Attempt {attempt}/{_max_attempts} failed for "
                        f"{func.__name__}: {e}. Retrying in {delay:.2f}s"
                    )
                    time.sleep(delay)

            raise last_exception  # type: ignore

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            """Async wrapper for retry logic."""
            last_exception = None

            for attempt in range(1, _max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == _max_attempts:
                        logger.error(
                            f"Retry exhausted for {func.__name__} "
                            f"after {_max_attempts} attempts"
                        )
                        raise

                    delay = min(
                        _base_delay * (_exponential_base ** (attempt - 1)),
                        _max_delay,
                    )

                    if _jitter:
                        delay *= 0.5 + random.random()  # nosec B311

                    logger.warning(
                        f"Attempt {attempt}/{_max_attempts} failed for "
                        f"{func.__name__}: {e}. Retrying in {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)

            raise last_exception  # type: ignore

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return wrapper  # type: ignore

    return decorator


def timeout(seconds: float) -> Callable[[F], F]:
    """
    Decorator to add timeout to async functions.

    Args:
        seconds: Maximum execution time in seconds

    Example:
        ```python
        @timeout(5.0)
        async def slow_operation():
            await asyncio.sleep(10)  # Will raise TimeoutError
        ```
    """

    def decorator(func: F) -> F:
        """Apply timeout logic to the async function."""
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("timeout decorator only works with async functions")

        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            """Async wrapper enforcing the timeout limit."""
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=seconds,
                )
            except asyncio.TimeoutError as err:
                raise TimeoutError(
                    f"Operation {func.__name__} timed out after {seconds}s"
                ) from err

        return wrapper  # type: ignore

    return decorator
