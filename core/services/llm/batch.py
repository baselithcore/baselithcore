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
cost controller is likewise out of scope. Callers own their own *request*
budgets.

The tenant's cumulative ledger is not a request budget, and this path does
book it: a batch job is the largest single spend the service can make, and
for a while it was the only provider call here that no gate stood in front
of and no ledger recorded — batch spend was invisible to the tenant cost cap
rather than merely mispriced. It is metered at the batch rate (50%), so the
ledger matches the invoice. The sequential fallback meters through
``generate_response`` at the full interactive rate, which is correct: it
buys no discount.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm._accounting import record_usage_cost
from core.services.llm._telemetry import gen_ai_system, record_genai_metrics
from core.services.llm.model_capabilities import default_max_tokens
from core.services.llm.usage import Usage

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)

_POLL_SECONDS = 10.0


@dataclass(frozen=True)
class BatchPrompt:
    """One prompt in a batch, keyed by a caller-chosen ``custom_id``.

    Attributes:
        custom_id: Caller-chosen id; batch results key on it.
        prompt: The user turn.
        system_prompt: Optional system prompt for this entry.
        max_tokens: Output cap; ``None`` uses the model family's recommended
            default rather than a fixed 4096, which truncated long entries.
        metadata: Free-form caller metadata (not sent upstream).
    """

    custom_id: str
    prompt: str
    system_prompt: str | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BatchCompletion:
    """Outcome for one batch entry (``succeeded`` → ``text`` populated).

    Attributes:
        custom_id: The id of the prompt this answers.
        text: The answer, when the entry succeeded.
        succeeded: Whether the entry produced an answer.
        error: Provider-reported failure type, when it did not.
        usage: Token accounting for this entry. Batches are billed at half
            price *per entry*, so a job's cost can only be reconstructed from
            the per-entry numbers — a batch that reported nothing could not be
            costed at all.
    """

    custom_id: str
    text: str | None
    succeeded: bool
    error: str | None = None
    usage: Usage = field(default_factory=Usage)


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
            "max_tokens": p.max_tokens or default_max_tokens(model),
            "messages": [{"role": "user", "content": p.prompt}],
        }
        if p.system_prompt:
            params["system"] = p.system_prompt
        requests.append({"custom_id": p.custom_id, "params": params})

    # Gate on the ambient tenant's cumulative USD budget BEFORE submitting.
    # Nothing stood in front of this call: a tenant over its cap could still
    # submit an unbounded batch job, which is the single largest spend this
    # service is able to make.
    from core.quotas.cost_enforcement import enforce_tenant_cost_budget

    await enforce_tenant_cost_budget(model=model)

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
                custom_id=result.custom_id,
                text=text,
                succeeded=True,
                usage=Usage.from_anthropic(getattr(message, "usage", None)),
            )
        else:
            completions[result.custom_id] = BatchCompletion(
                custom_id=result.custom_id,
                text=None,
                succeeded=False,
                error=result.result.type,
            )
    # Return in submission order; missing ids (shouldn't happen) marked failed.
    ordered = [
        completions.get(
            p.custom_id,
            BatchCompletion(p.custom_id, None, False, error="missing_result"),
        )
        for p in prompts
    ]
    await _meter_batch_job(service, model, ordered)
    return ordered


async def _meter_batch_job(
    service: LLMService, model: str, completions: list[BatchCompletion]
) -> None:
    """Book a finished batch job's spend at the batch rate.

    Args:
        service: The owning service (for the ``gen_ai.system`` label).
        model: The model the job ran on.
        completions: Every entry's outcome; only metered successes cost money.
    """
    metered = [c.usage for c in completions if c.succeeded and not c.usage.is_empty]
    if not metered:
        return

    system = gen_ai_system(getattr(service.config, "provider", None))
    total = Usage()
    for usage in metered:
        # Per entry, because each entry is one call: an aggregate observation
        # would misreport the per-call token distribution.
        record_genai_metrics(
            system,
            model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            batch=True,
        )
        total = total.merge(usage)

    # One ledger write per job, not per entry: a ten-thousand-entry batch must
    # not become ten thousand quota-store round trips. Never raises.
    await record_usage_cost(model, total, batch=True)


async def _sequential_fallback(
    service: LLMService, prompts: list[BatchPrompt], model: str
) -> list[BatchCompletion]:
    """Provider-agnostic fallback: sequential calls, same result shape."""
    out: list[BatchCompletion] = []
    for p in prompts:
        try:
            # One sink per entry: a batch is costed per ``custom_id``, so a
            # shared accumulator would attribute every entry to the last one.
            usage_sink: list[Usage] = []
            text = await service.generate_response(
                p.prompt,
                model=model,
                system_prompt=p.system_prompt,
                max_tokens=p.max_tokens,
                usage_sink=usage_sink,
            )
            out.append(
                BatchCompletion(
                    p.custom_id,
                    text,
                    True,
                    usage=usage_sink[-1] if usage_sink else Usage(),
                )
            )
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
    from core.services.llm._late_binding import governed_target

    # A funnel-issued service answers for whoever is calling now.
    service = governed_target(service)
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
