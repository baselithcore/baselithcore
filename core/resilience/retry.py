"""
Retry and Timeout decorators.

Provides resilience patterns for:
- Retry with exponential backoff
- Timeout for async operations
"""

import asyncio
import builtins
import functools
import random
import time
from collections.abc import Callable
from typing import Any, NoReturn, ParamSpec, TypeVar, cast

from core.config.resilience import get_resilience_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


class TimeoutError(Exception):
    """Raised when operation times out."""

    pass


def _describe(exc: BaseException) -> str:
    """Render an exception for a log line, never as an empty string.

    Several client libraries raise with no message at all (``httpx.ReadTimeout``,
    ``ConnectError``), which turned the retry logs into "failed for f: ." — the
    one detail an operator needs (*what* went wrong) missing. Always lead with
    the type, appending the message only when there is one.
    """
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _server_requested_delay(exc: BaseException) -> float | None:
    """Seconds the server asked us to wait, if the exception carries them.

    Reads a ``retry_after`` attribute — set by callers that parsed the RFC 9110
    ``Retry-After`` header (see
    :class:`core.services.llm.exceptions.RateLimitError`). Duck-typed on
    purpose so this module stays free of any dependency on the LLM stack, and
    so any other error type can opt in by exposing the same attribute.

    Returns ``None`` when absent or not a usable positive number, in which case
    the caller falls back to its own backoff curve.
    """
    raw = getattr(exc, "retry_after", None)
    if raw is None or isinstance(raw, bool):
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def _next_delay(
    exc: BaseException,
    attempt: int,
    *,
    base_delay: float,
    exponential_base: float,
    max_delay: float,
    jitter: bool,
) -> float:
    """How long to wait before ``attempt + 1``.

    Shared by the sync and async wrappers on purpose: keeping two copies of
    this is what previously let ``Retry-After`` support land on one path only.

    A server that told us how long to wait (RFC 9110 ``Retry-After``) knows
    better than our curve — retrying before its window re-sends into a closed
    door and, with many providers, extends the throttle. That instruction is
    still bounded by ``max_delay`` and is *not* jittered: it is an explicit
    directive, not an estimate to spread out. Absent it, fall back to
    exponential backoff with optional jitter.
    """
    server_delay = _server_requested_delay(exc)
    if server_delay is not None:
        return min(server_delay, max_delay)
    delay = min(base_delay * (exponential_base ** (attempt - 1)), max_delay)
    if jitter:
        delay *= 0.5 + random.random()  # nosec B311
    return delay


def _raise_last_exception(last_exception: BaseException | None) -> NoReturn:
    """Re-raise the last captured exception."""
    if last_exception is None:
        raise RuntimeError("Retry exhausted without a captured exception.")
    raise last_exception


def retry(
    max_attempts: int | None = None,
    base_delay: float | None = None,
    max_delay: float | None = None,
    exponential_base: float | None = None,
    jitter: bool | None = None,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
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

    def decorator(func: Callable[P, Any]) -> Callable[P, Any]:
        """Apply retry logic to the function."""

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            """Sync wrapper for retry logic."""
            last_exception: BaseException | None = None

            for attempt in range(1, _max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == _max_attempts:
                        logger.error(
                            f"Retry exhausted for {func.__name__} "
                            f"after {_max_attempts} attempts: {_describe(e)}"
                        )
                        raise

                    delay = _next_delay(
                        e,
                        attempt,
                        base_delay=_base_delay,
                        exponential_base=_exponential_base,
                        max_delay=_max_delay,
                        jitter=_jitter,
                    )

                    logger.warning(
                        f"Attempt {attempt}/{_max_attempts} failed for "
                        f"{func.__name__}: {_describe(e)}. Retrying in {delay:.2f}s"
                    )
                    time.sleep(delay)

            _raise_last_exception(last_exception)

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            """Async wrapper for retry logic."""
            last_exception: BaseException | None = None

            for attempt in range(1, _max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == _max_attempts:
                        logger.error(
                            f"Retry exhausted for {func.__name__} "
                            f"after {_max_attempts} attempts: {_describe(e)}"
                        )
                        raise

                    delay = _next_delay(
                        e,
                        attempt,
                        base_delay=_base_delay,
                        exponential_base=_exponential_base,
                        max_delay=_max_delay,
                        jitter=_jitter,
                    )

                    logger.warning(
                        f"Attempt {attempt}/{_max_attempts} failed for "
                        f"{func.__name__}: {_describe(e)}. Retrying in {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)

            _raise_last_exception(last_exception)

        if asyncio.iscoroutinefunction(func):
            return cast(Callable[P, Any], async_wrapper)
        return cast(Callable[P, Any], wrapper)

    return decorator


def timeout(seconds: float) -> Callable[[Callable[P, Any]], Callable[P, Any]]:
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

    def decorator(func: Callable[P, Any]) -> Callable[P, Any]:
        """Apply timeout logic to the async function."""
        if not asyncio.iscoroutinefunction(func):
            raise TypeError("timeout decorator only works with async functions")

        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            """Async wrapper enforcing the timeout limit."""
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=seconds,
                )
            except builtins.TimeoutError as err:
                raise TimeoutError(
                    f"Operation {func.__name__} timed out after {seconds}s"
                ) from err

        return cast(Callable[P, Any], wrapper)

    return decorator
