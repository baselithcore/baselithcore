"""Batch generation over the Anthropic Message Batches API.

Offline workloads (evaluation replays, memory consolidation summaries,
dataset labeling) don't need interactive latency — the Message Batches API
processes them asynchronously at **50% of standard token prices**. This
module adds a provider-neutral seam:

* :class:`BatchPrompt` / :class:`BatchCompletion` — neutral request/result
  types keyed by ``custom_id`` (batch results arrive in arbitrary order —
  never rely on position).
* :func:`generate_batch` — routes to the Anthropic Batches API when the
  active provider supports it, otherwise falls back to sequential
  ``generate_response`` calls (same results, no cost saving) so callers
  never need provider-specific branches.

Security/cost posture: batch calls bypass the per-request LoopBudget by
design (they are offline jobs, not orchestrated requests); the middleware
cost controller is likewise out of scope. Callers own their own budgets.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

_POLL_SECONDS = 10.0


@dataclass(frozen=True)
class BatchPrompt:
    """One prompt in a batch, keyed by a caller-chosen ``custom_id``."""

    custom_id: str
    prompt: str
    system_prompt: str | None = None
    max_tokens: int = 4096
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchCompletion:
    """Outcome for one batch entry (``succeeded`` → ``text`` populated)."""

    custom_id: str
    text: str | None
    succeeded: bool
    error: str | None = None


async def _anthropic_batch(
    service: LLMService,
    prompts: list[BatchPrompt],
    model: str,
    *,
    poll_seconds: float,
    timeout_seconds: float,
) -> list[BatchCompletion]:
    """Submit to the Anthropic Message Batches API and poll to completion."""
    # Duck-typed: only AnthropicProvider exposes the underlying AsyncAnthropic
    # client; generate_batch routes here only for that provider.
    client: Any = service.provider._ensure_client()  # type: ignore[attr-defined]

    requests = []
    for p in prompts:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": p.max_tokens,
            "messages": [{"role": "user", "content": p.prompt}],
        }
        if p.system_prompt:
            params["system"] = p.system_prompt
        requests.append({"custom_id": p.custom_id, "params": params})

    batch = await client.messages.batches.create(requests=requests)
    logger.info(
        "llm_batch_submitted", extra={"batch_id": batch.id, "count": len(requests)}
    )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while batch.processing_status != "ended":
        if loop.time() > deadline:
            raise TimeoutError(
                f"Anthropic batch {batch.id} still "
                f"{batch.processing_status!r} after {timeout_seconds}s"
            )
        await asyncio.sleep(poll_seconds)
        batch = await client.messages.batches.retrieve(batch.id)

    completions: dict[str, BatchCompletion] = {}
    async for result in client.messages.batches.results(batch.id):
        if result.result.type == "succeeded":
            message = result.result.message
            text = next((b.text for b in message.content if b.type == "text"), "")
            completions[result.custom_id] = BatchCompletion(
                custom_id=result.custom_id, text=text, succeeded=True
            )
        else:
            completions[result.custom_id] = BatchCompletion(
                custom_id=result.custom_id,
                text=None,
                succeeded=False,
                error=result.result.type,
            )
    # Return in submission order; missing ids (shouldn't happen) marked failed.
    return [
        completions.get(
            p.custom_id,
            BatchCompletion(p.custom_id, None, False, error="missing_result"),
        )
        for p in prompts
    ]


async def _sequential_fallback(
    service: LLMService, prompts: list[BatchPrompt], model: str
) -> list[BatchCompletion]:
    """Provider-agnostic fallback: sequential calls, same result shape."""
    out: list[BatchCompletion] = []
    for p in prompts:
        try:
            text = await service.generate_response(
                p.prompt,
                model=model,
                system_prompt=p.system_prompt,
                max_tokens=p.max_tokens,
            )
            out.append(BatchCompletion(p.custom_id, text, True))
        except Exception as exc:
            out.append(BatchCompletion(p.custom_id, None, False, error=str(exc)))
    return out


async def generate_batch(
    service: LLMService,
    prompts: list[BatchPrompt],
    *,
    model: str | None = None,
    poll_seconds: float = _POLL_SECONDS,
    timeout_seconds: float = 24 * 3600.0,
) -> list[BatchCompletion]:
    """Generate completions for *prompts*, batched where the provider allows.

    Anthropic: one Message Batches job (50% price, results within 24h,
    usually much sooner). Other providers: sequential fallback with identical
    result shape. Results are returned in submission order regardless of the
    order the provider produced them.

    Raises:
        ValueError: Duplicate ``custom_id``s (batch results key on them).
        TimeoutError: Batch not finished within ``timeout_seconds``.
    """
    if not prompts:
        return []
    ids = [p.custom_id for p in prompts]
    if len(set(ids)) != len(ids):
        raise ValueError("BatchPrompt.custom_id values must be unique")

    resolved_model = service._resolve_model(model)
    provider_name = (service.config.provider or "").lower()
    if provider_name == "anthropic":
        return await _anthropic_batch(
            service,
            prompts,
            resolved_model,
            poll_seconds=poll_seconds,
            timeout_seconds=timeout_seconds,
        )
    logger.info(
        "llm_batch_sequential_fallback",
        extra={"provider": provider_name, "count": len(prompts)},
    )
    return await _sequential_fallback(service, prompts, resolved_model)


__all__ = ["BatchCompletion", "BatchPrompt", "generate_batch"]
