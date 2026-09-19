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
from core.services.llm._fallback_support import (
    _breaker_open,
    _clone_service,
    fatal_exception_types,
    parse_fallback_chain,
    record_fallback_served,
)
from core.services.llm.exceptions import LLMProviderError, describe_exception

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

#: One provider chunk: the text delta and the cumulative token count.
Chunk = tuple[str, int]

#: Never an empty string: every httpx timeout class stringifies to ``""``,
#: which is exactly what a hung local model server produces.
_describe = describe_exception


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
            max_tokens, and the ``usage_sink`` list a provider appends its
            metered usage to) passed through unchanged to every stage. Every
            stage shares the sink, so the serving stage's record is its last
            entry.

    Returns:
        ``(chunks, serving_service, serving_provider, serving_model)`` — the
        stream to consume plus who is actually serving it, so the caller can
        attribute telemetry and cost to the provider that answered rather
        than to the one it asked first.

    Raises:
        LLMProviderError: When every candidate failed to produce a first
            chunk. The message carries **every** stage's failure, primary
            first: reporting only the last one named the local fallback as the
            cause of an outage that started at the hosted primary, which sent
            operators to debug the wrong host.
        BudgetExceededError: Propagated unchanged from any stage — budget
            and deadline overruns never fall through.
    """
    fatal = fatal_exception_types()
    primary = service.config.provider
    failures: list[str] = []

    for provider, use_model, existing in _candidates(service, model):
        if _breaker_open(provider):
            failures.append(f"{provider}:{use_model} (circuit_open)")
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
            failures.append(f"{provider}:{use_model} ({_describe(exc)})")
            await _aclose(stream)
            logger.warning(
                "llm_stream_provider_failed",
                extra={"provider": provider, "error": _describe(exc)},
            )
            continue

        if provider != primary:
            record_fallback_served(
                primary=primary,
                served_by=provider,
                served_model=use_model,
                path="stream",
            )
        return _prepend(first, stream), serving, provider, use_model

    raise LLMProviderError(
        f"All stream providers failed: {', '.join(failures) or '<empty>'}"
    )


__all__ = ["Chunk", "open_stream"]
