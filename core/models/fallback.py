"""
Provider fallback chain for LLM (and tool) calls.

Implements the resilience pattern from book ch7: when the primary provider
is unhealthy or rate-limited, fall through to a secondary, then a local
fallback. The chain composes with ``CircuitBreaker`` so an open breaker
skips its provider without paying for a doomed call.

The chain is provider-agnostic. Each ``Provider`` is just a name plus an
async callable; the chain has no knowledge of OpenAI/Anthropic/Ollama
specifics. Integrating LiteLLM, a vendor SDK, or a mock is identical.

Failure metrics are surfaced via ``FallbackOutcome.attempts`` so callers
can log a structured trail for observability.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Generic, TypeVar

from core.observability.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

ProviderCall = Callable[..., Awaitable[T] | T]
BreakerCheck = Callable[[], bool]


class AllProvidersFailedError(RuntimeError):
    """Raised when every provider in the chain failed or was skipped."""

    def __init__(self, attempts: "list[ProviderAttempt]") -> None:
        names = ", ".join(a.provider for a in attempts) or "<empty>"
        super().__init__(f"All providers failed: {names}")
        self.attempts = attempts


@dataclass(frozen=True)
class Provider(Generic[T]):
    """One provider stage in the fallback chain."""

    name: str
    call: ProviderCall[T]
    is_open: BreakerCheck | None = None


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
        return await result  # type: ignore[no-any-return]
    return result  # type: ignore[return-value]


class FallbackChain(Generic[T]):
    """Ordered list of providers tried in sequence on failure."""

    def __init__(self, providers: list[Provider[T]]) -> None:
        if not providers:
            raise ValueError("FallbackChain requires at least one provider")
        names = [p.name for p in providers]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate provider names in chain: {names}")
        self._providers = providers

    async def run(self, *args: object, **kwargs: object) -> FallbackOutcome[T]:
        """Run the chain. Returns the first successful provider's result."""
        attempts: list[ProviderAttempt] = []
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
            try:
                result = await _invoke(provider.call, *args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — broad by design
                attempts.append(
                    ProviderAttempt(
                        provider=provider.name,
                        succeeded=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                logger.warning(
                    "fallback_provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
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
