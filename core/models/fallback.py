"""
Provider fallback chain for LLM (and tool) calls.

When the primary provider is unhealthy or rate-limited, fall through to
a secondary, then a local fallback. The chain composes with
``CircuitBreaker`` so an open breaker skips its provider without paying
for a doomed call.

The chain is provider-agnostic. Each ``Provider`` is just a name plus an
async callable; the chain has no knowledge of OpenAI/Anthropic/Ollama
specifics. Integrating LiteLLM, a vendor SDK, or a mock is identical.

Failure metrics are surfaced via ``FallbackOutcome.attempts`` so callers
can log a structured trail for observability.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from core.observability.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

ProviderCall = Callable[..., Awaitable[T] | T]
BreakerCheck = Callable[[], bool]


def _describe(exc: BaseException) -> str:
    """``Type: message``, or just ``Type`` when the exception carries none.

    A bare ``{exc}`` is empty for the failures that matter most here: an
    ``asyncio.TimeoutError`` from a stage deadline, and every ``httpx`` timeout
    class, all stringify to ``""``. Logging that produced ``error: ''`` — a
    chain that fell through for no stated reason.
    """
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class AllProvidersFailedError(RuntimeError):
    """Raised when every provider in the chain failed or was skipped."""

    def __init__(self, attempts: list[ProviderAttempt]) -> None:
        # Carry *why* each stage failed, not just which ones did: this message
        # is often the only thing that reaches a caller, and a bare list of
        # names tells them nothing they can act on.
        detail = ", ".join(
            f"{a.provider} ({a.error})" if a.error else a.provider for a in attempts
        )
        super().__init__(f"All providers failed: {detail or '<empty>'}")
        self.attempts = attempts


@dataclass(frozen=True)
class Provider(Generic[T]):
    """One provider stage in the fallback chain.

    ``timeout_seconds`` bounds this stage only, overriding the chain-level
    ``stage_timeout_seconds`` when set.
    """

    name: str
    call: ProviderCall[T]
    is_open: BreakerCheck | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class ProviderAttempt:
    """Record of a single provider attempt during a chain run."""

    provider: str
    succeeded: bool
    error: str | None = None
    skipped: bool = False


@dataclass(frozen=True)
class FallbackOutcome(Generic[T]):
    """Successful chain outcome plus the trail of attempts that produced it."""

    result: T
    provider: str
    attempts: list[ProviderAttempt] = field(default_factory=list)


async def _invoke(call: ProviderCall[T], *args: object, **kwargs: object) -> T:
    """Invoke a sync or async ``call`` and await if needed."""
    result = call(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class FallbackChain(Generic[T]):
    """Ordered list of providers tried in sequence on failure."""

    def __init__(
        self,
        providers: list[Provider[T]],
        fatal_exceptions: tuple[type[BaseException], ...] = (),
        stage_timeout_seconds: float | None = None,
        total_timeout_seconds: float | None = None,
    ) -> None:
        """Args:
        providers: Ordered stages, tried in sequence on failure.
        fatal_exceptions: Exception types that abort the whole chain
            (budget/deadline blown — falling through would spend money the
            caller no longer has).
        stage_timeout_seconds: Per-stage latency bound. Without one the
            worst-case chain latency is the SUM of every provider SDK
            timeout; with it a hung stage becomes a failed attempt and the
            chain falls through. ``Provider.timeout_seconds`` overrides it
            per stage. None keeps the legacy unbounded behavior.
        total_timeout_seconds: Wall-clock budget for the whole chain. Each
            stage gets at most the time left, and once the budget is spent the
            remaining stages are skipped instead of run. This is what bounds
            the chain when no per-stage timeout is configured: three stages ×
            (SDK timeout + retry backoff) otherwise runs for many minutes,
            long past the point the caller and any reverse proxy gave up.
        """
        if not providers:
            raise ValueError("FallbackChain requires at least one provider")
        names = [p.name for p in providers]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate provider names in chain: {names}")
        self._providers = providers
        self._fatal_exceptions = fatal_exceptions
        self._stage_timeout_seconds = stage_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds

    def _remaining(self, started: float) -> float | None:
        """Seconds left in the chain budget, or None when unbounded."""
        if self._total_timeout_seconds is None:
            return None
        return self._total_timeout_seconds - (time.monotonic() - started)

    async def run(self, *args: object, **kwargs: object) -> FallbackOutcome[T]:
        """Run the chain. Returns the first successful provider's result."""
        attempts: list[ProviderAttempt] = []
        started = time.monotonic()
        for provider in self._providers:
            if provider.is_open is not None and provider.is_open():
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        succeeded=False,
                        error="circuit_open",
                        skipped=True,
                    )
                )
                logger.info(
                    "fallback_skip_open",
                    extra={"provider": provider.name},
                )
                continue

            remaining = self._remaining(started)
            if remaining is not None and remaining <= 0:
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        succeeded=False,
                        error="chain_deadline_exceeded",
                        skipped=True,
                    )
                )
                logger.warning(
                    "fallback_skip_deadline",
                    extra={"provider": provider.name},
                )
                continue

            timeout = (
                provider.timeout_seconds
                if provider.timeout_seconds is not None
                else self._stage_timeout_seconds
            )
            # Never let one stage outlive the chain budget.
            if remaining is not None:
                timeout = remaining if timeout is None else min(timeout, remaining)
            try:
                if timeout is not None:
                    result = await asyncio.wait_for(
                        _invoke(provider.call, *args, **kwargs), timeout=timeout
                    )
                else:
                    result = await _invoke(provider.call, *args, **kwargs)
            except Exception as exc:
                if isinstance(exc, self._fatal_exceptions):
                    # Fatal for the whole request (budget/deadline blown):
                    # falling through would spend money the caller no longer has.
                    raise
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        succeeded=False,
                        error=_describe(exc),
                    )
                )
                logger.warning(
                    "fallback_provider_failed",
                    extra={"provider": provider.name, "error": _describe(exc)},
                )
                continue
            attempts.append(ProviderAttempt(provider=provider.name, succeeded=True))
            return FallbackOutcome(
                result=result, provider=provider.name, attempts=attempts
            )
        raise AllProvidersFailedError(attempts)

    @property
    def providers(self) -> list[Provider[T]]:
        return list(self._providers)
