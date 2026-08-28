"""Cross-provider failover for the streaming path.

The buffered and structured paths fall through ``LLMConfig.fallback_chain``
(:mod:`core.services.llm.fallback_runtime`); the streaming path did not. One
unreachable provider therefore produced a split reality that reads as a bug
in the *feature*, not in the funnel: buffered calls kept working off the
chain while every streaming surface — chat, in-character interviews, any
token-by-token UI — died with the primary provider's connection error.

Failover here is possible only **before the first chunk reaches the
caller**. Once a token has been yielded the response is committed: the
consumer has already rendered it, and restarting on another provider would
duplicate or contradict what it showed. So each candidate is opened and its
first chunk awaited; a failure at that point is invisible to the consumer
and switches provider, while a failure afterwards propagates unchanged.

The typed event stream (``stream_events``) deliberately keeps its own path:
a fallback provider may not support native tool-call streaming, so failing
over there could silently change the contract the caller is consuming.

Budget and deadline errors never fall through — the same rule the buffered
path applies: a request that ran out of budget must not spend more of it on
a second provider.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm._deadline import stream_within_deadline
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.fallback_runtime import (
    _breaker_open,
    _clone_service,
    parse_fallback_chain,
)

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

#: One provider chunk: the text delta and the cumulative token count.
Chunk = tuple[str, int]


def _fatal_exception_types() -> tuple[type[BaseException], ...]:
    """Exceptions that must abort the chain instead of trying the next stage."""
    from core.middleware.cost_control import (
        BudgetExceededError as MiddlewareBudgetExceededError,
    )
    from core.orchestration.limits import BudgetExceededError as LoopBudgetExceededError
    from core.services.llm.exceptions import BudgetExceededError

    return (
        BudgetExceededError,
        MiddlewareBudgetExceededError,
        LoopBudgetExceededError,
    )


def _candidates(
    service: LLMService, model: str
) -> Iterator[tuple[str, str, LLMService | None]]:
    """Yield ``(provider, model, service)`` stages, primary first.

    A ``None`` service means "clone lazily" — cloning costs a provider
    construction, so it only happens for a stage actually attempted.
    """
    primary = service.config.provider
    yield primary, model, service

    chain_spec = getattr(service.config, "fallback_chain", "")
    # The isinstance guard mirrors fallback_runtime: Mock/SimpleNamespace test
    # configs expose truthy attributes that must not enable fallback.
    if not isinstance(chain_spec, str) or not chain_spec:
        return
    for provider, fallback_model in parse_fallback_chain(chain_spec):
        if provider == primary and fallback_model == model:
            continue  # identical to the primary stage — nothing to gain
        yield provider, fallback_model, None


async def _empty() -> AsyncIterator[Chunk]:
    """A stream that ends immediately (provider returned no chunks)."""
    return
    yield  # pragma: no cover — unreachable, makes this an async generator


async def _prepend(first: Chunk, rest: AsyncIterator[Chunk]) -> AsyncIterator[Chunk]:
    """Re-yield the peeked chunk, then the remainder of the stream."""
    yield first
    async for chunk in rest:
        yield chunk


async def _aclose(stream: AsyncIterator[Chunk]) -> None:
    """Release a stream that failed, ignoring close-time errors."""
    closer = getattr(stream, "aclose", None)
    if closer is None:
        return
    try:
        await closer()
    except Exception as exc:  # closing an already-dead stream may fail
        logger.debug("llm_stream_close_failed", extra={"error": str(exc)})


async def open_stream(
    service: LLMService,
    prompt: str,
    model: str,
    stream_kwargs: dict[str, Any],
) -> tuple[AsyncIterator[Chunk], LLMService, str, str]:
    """Open a token stream, failing over the chain before the first chunk.

    Args:
        service: The service whose configured provider is the primary stage.
        prompt: Prompt to stream a completion for.
        model: Already-resolved model for the primary stage.
        stream_kwargs: Extra provider kwargs (system prompt, temperature,
            max_tokens) passed through unchanged to every stage.

    Returns:
        ``(chunks, serving_service, serving_provider, serving_model)`` — the
        stream to consume plus who is actually serving it, so the caller can
        attribute telemetry and cost to the provider that answered rather
        than to the one it asked first.

    Raises:
        LLMProviderError: When every candidate failed to produce a first
            chunk (the error carries the last failure).
        BudgetExceededError: Propagated unchanged from any stage — budget
            and deadline overruns never fall through.
    """
    fatal = _fatal_exception_types()
    primary = service.config.provider
    last_error: Exception | None = None

    for provider, use_model, existing in _candidates(service, model):
        if _breaker_open(provider):
            logger.warning("llm_stream_provider_skipped", extra={"provider": provider})
            continue
        serving = existing or _clone_service(service, provider, use_model)
        # Deadline applies from the first chunk on: a provider that stalls
        # before emitting anything must fail over, not hang the request.
        stream = stream_within_deadline(
            serving.provider.generate_stream(
                prompt=prompt, model=use_model, **stream_kwargs
            )
        )
        try:
            first = await stream.__anext__()
        except StopAsyncIteration:
            # Empty but healthy: the provider answered with no content.
            return _empty(), serving, provider, use_model
        except fatal:
            await _aclose(stream)
            raise
        except Exception as exc:  # any failure to open tries the next stage
            last_error = exc
            await _aclose(stream)
            logger.warning(
                "llm_stream_provider_failed",
                extra={"provider": provider, "error": str(exc)},
            )
            continue

        if provider != primary:
            logger.warning(
                "llm_stream_fallback_served",
                extra={"provider": provider, "primary": primary},
            )
        return _prepend(first, stream), serving, provider, use_model

    raise LLMProviderError(f"All stream providers failed: {last_error}")


__all__ = ["Chunk", "open_stream"]
