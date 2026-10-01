"""Observers for every *billed* LLM turn, carrying the full usage record.

The token-report seam (:func:`core.services.llm.register_token_sink`) streams
bare ``(count, model)`` pairs: the prompt side as a local tokenizer estimate
under an ``input`` sentinel, the output side as "total minus that estimate",
one report per streamed chunk. A consumer that prices those pairs is wrong in
three ways it cannot correct on its own — the prompt is a guess, cached prompt
tokens are priced at the full input rate (a cache read bills at ~0.1x), and a
batch job never reports at all, so its spend is invisible.

A usage sink instead receives one :class:`UsageReport` per completed turn, at
the same point the tenant cost ledger books it (``record_usage_cost``): the
provider's metered four-bucket :class:`~core.services.llm.usage.Usage` (or an
explicitly ``estimated`` one when the provider reported nothing), the model
that actually answered, whether it was billed at the batch rate, and how many
calls it stands for. Engines outside the funnel reach it through
:func:`core.services.llm.report_external_usage`.

Sinks run in the caller's context, so the identity and plugin bound to the
request (``core.context``) are visible to them. They are best-effort
observers: what they raise is swallowed, and they never block a call.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core.observability.logging import get_logger
from core.services.llm.usage import Usage

logger = get_logger(__name__)

__all__ = [
    "UsageReport",
    "UsageSink",
    "emit_usage_report",
    "register_usage_sink",
    "unregister_usage_sink",
]


@dataclass(frozen=True, slots=True)
class UsageReport:
    """One billed turn, as delivered to every usage sink.

    Attributes:
        model: The model that served the turn, provider-qualified when the
            provider is local (``ollama/<tag>``) so it prices as a known zero.
        usage: The four-bucket record the tenant ledger priced.
        batch: True when a batch API served it (half the interactive rate).
        requests: How many model calls the record stands for — one for an
            interactive turn, the number of metered entries for a batch job.
    """

    model: str
    usage: Usage
    batch: bool = False
    requests: int = 1


UsageSink = Callable[[UsageReport], None]
_usage_sinks: list[UsageSink] = []


def register_usage_sink(sink: UsageSink) -> None:
    """Subscribe *sink* to every billed turn. Idempotent."""
    if sink not in _usage_sinks:
        _usage_sinks.append(sink)


def unregister_usage_sink(sink: UsageSink) -> None:
    """Remove a previously registered sink (no-op when absent)."""
    if sink in _usage_sinks:
        _usage_sinks.remove(sink)


def emit_usage_report(report: UsageReport) -> None:
    """Deliver *report* to every registered sink. Never raises.

    An empty record is not delivered: a cache hit or a call the provider
    never answered moved no billed tokens.
    """
    if report.usage.is_empty:
        return
    for sink in list(_usage_sinks):
        try:
            sink(report)
        except Exception as exc:
            logger.debug("usage sink failed", sink=repr(sink), error=str(exc))
