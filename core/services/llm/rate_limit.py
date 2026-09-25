"""Opt-in client-side rate limit on outgoing LLM calls.

``RESILIENCE_LLM_RATE_LIMIT`` / ``RESILIENCE_LLM_RATE_WINDOW`` used to seed
:func:`core.resilience.get_llm_limiter` and nothing else: no generation path
asked it, so the settings throttled nothing. :func:`acquire_llm_call_slot` is
the one gate every generation path awaits right after the tenant cost gate —
text, structured / native tool calling, the message API, both streaming
flavours, image generation and the Anthropic batch submission — so a call
refused by the budget never spends a slot, and a cache hit never reaches it.

Semantics:

* **Off by default** (``RESILIENCE_LLM_RATE_ENABLED=false``): the gate is one
  cached-config attribute read and returns.
* **One slot per logical call.** Transport retries inside
  ``_generate_with_retry`` and fallback-chain stages do not take extra slots.
* **Waits, then fails.** Over the limit, the caller sleeps (``asyncio.sleep``,
  never blocking the loop) until the window frees a slot, for at most
  ``RESILIENCE_LLM_RATE_MAX_WAIT`` seconds in total; past that it raises
  :class:`LocalLLMRateLimitError`, a :class:`LLMRateLimitError` subclass, so
  ``except RateLimitError`` call sites keep working.
* **Scope.** One window per provider name (``RESILIENCE_LLM_RATE_PER_PROVIDER``,
  default on) or one shared window. Per worker process with the default local
  cache backend; ``CACHE_BACKEND=redis`` backs it with
  :class:`~core.resilience.RedisRateLimiter`, so every worker shares one
  window (its sync client runs off the event loop, and it degrades to the
  in-process window when Redis is unreachable).

The limiter is built on first use from the settings of that moment; call
:func:`reset_llm_rate_limiter` after changing them at runtime.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from core.config.resilience import get_resilience_config
from core.observability.logging import get_logger
from core.resilience.rate_limiter import (
    InMemoryRateLimiter,
    RateLimiter,
    RateLimiterBackend,
    get_llm_limiter,
)
from core.services.llm.errors import LLMRateLimitError

logger = get_logger(__name__)

__all__ = [
    "LocalLLMRateLimitError",
    "acquire_llm_call_slot",
    "llm_rate_limit_key",
    "reset_llm_rate_limiter",
]

#: Floor on one sleep, so a backend answering ``retry_after=0`` at a window
#: boundary cannot turn the wait into a busy loop.
_MIN_SLEEP_SECONDS = 0.05

#: Key used when every provider shares one window.
_SHARED_KEY = "llm:*"


class LocalLLMRateLimitError(LLMRateLimitError):
    """The client-side LLM rate limit had no free slot within the wait bound.

    Raised before any provider is contacted, so nothing was spent. Unlike a
    provider 429 it carries no status code.

    Attributes:
        key: The limiter key that was full (``llm:<provider>`` or ``llm:*``).
        limit: Calls allowed per window.
        window: Window length in seconds.
        retry_after: Seconds until the window frees a slot, when known.
    """

    def __init__(
        self, key: str, limit: int, window: int, retry_after: float | None
    ) -> None:
        wait = f"; retry in {retry_after:.1f}s" if retry_after is not None else ""
        super().__init__(
            f"client-side LLM rate limit reached for {key!r}: {limit} calls "
            f"per {window}s (RESILIENCE_LLM_RATE_LIMIT){wait}",
            retry_after=retry_after,
            status_code=None,
        )
        self.key = key
        self.limit = limit
        self.window = window


@dataclass(frozen=True)
class _Limiter:
    """The process-wide limiter plus whether its backend does network I/O."""

    limiter: RateLimiter
    offload: bool


_state: _Limiter | None = None


def reset_llm_rate_limiter() -> None:
    """Drop the process-wide limiter; the next call rebuilds it from config."""
    global _state
    _state = None


def _redis_declared() -> bool:
    """Whether the deployment declares a shared Redis cache backend."""
    try:
        from core.config import get_storage_config

        return getattr(get_storage_config(), "cache_backend", "") == "redis"
    except Exception:  # silent-ok: no storage config means no declared Redis
        return False


async def _get_limiter() -> _Limiter:
    """Build the limiter on first use (Redis when ``CACHE_BACKEND=redis``)."""
    global _state
    if _state is not None:
        return _state
    backend: RateLimiterBackend
    offload = _redis_declared()
    if offload:
        from core.resilience.rate_limiter import RedisRateLimiter

        # The constructor pings Redis synchronously: keep it off the loop.
        backend = await asyncio.to_thread(RedisRateLimiter)
    else:
        backend = InMemoryRateLimiter()
    if _state is None:
        config = get_resilience_config()
        _state = _Limiter(
            limiter=get_llm_limiter(
                limit=max(1, config.llm_rate_limit),
                window=max(1, config.llm_rate_window),
                backend=backend,
            ),
            offload=offload,
        )
    return _state


def llm_rate_limit_key(provider: object, *, per_provider: bool = True) -> str:
    """Limiter key for *provider*.

    Args:
        provider: The service's configured provider name.
        per_provider: False collapses every provider into one window.

    Returns:
        ``llm:<provider>`` (lower-cased), or ``llm:*`` when shared or unnamed.
    """
    name = str(provider or "").strip().lower() if per_provider else ""
    return f"llm:{name}" if name else _SHARED_KEY


async def acquire_llm_call_slot(provider: object) -> None:
    """Take one slot of the LLM call rate limit, waiting if needed.

    A no-op unless ``RESILIENCE_LLM_RATE_ENABLED`` is true.

    Args:
        provider: The configured provider name of the calling service.

    Raises:
        LocalLLMRateLimitError: No slot freed up within
            ``RESILIENCE_LLM_RATE_MAX_WAIT`` seconds.
    """
    config = get_resilience_config()
    if not config.llm_rate_enabled:
        return
    state = await _get_limiter()
    limiter = state.limiter
    key = llm_rate_limit_key(provider, per_provider=config.llm_rate_per_provider)
    budget = max(0.0, config.llm_rate_max_wait)
    waited = 0.0
    while True:
        if state.offload:
            result = await asyncio.to_thread(limiter.check, key)
        else:
            result = limiter.check(key)
        if result.allowed:
            if waited:
                logger.info(
                    "llm_rate_limit_waited",
                    extra={"key": key, "waited_seconds": round(waited, 3)},
                )
            return
        delay = max(_MIN_SLEEP_SECONDS, result.retry_after or 0.0)
        if waited + delay > budget:
            logger.warning(
                "llm_rate_limit_exceeded",
                extra={"key": key, "limit": limiter.limit, "window": limiter.window},
            )
            raise LocalLLMRateLimitError(
                key, limiter.limit, limiter.window, result.retry_after
            )
        await asyncio.sleep(delay)
        waited += delay
