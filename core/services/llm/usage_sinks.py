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
request (``core.context``) are visible to them, and the bound tenant travels
on the report itself (``tenant_id``) for sinks that hand it off. They are
best-effort observers: what they raise is swallowed — logged at WARNING and
counted in ``mas_usage_sink_failures_total``, because a lost ledger write is
a billing gap nobody else would notice — and they never block a call. A sink
that must do I/O is an ``async def``: its coroutine is scheduled on the
running loop rather than awaited inline, so the turn that produced the
report is never held up by the ledger.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from core.context import get_current_tenant_id, tenant_is_bound
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
        tenant_id: The tenant bound to the context that produced the turn,
            or ``None`` outside any tenant (a background job, a script).
            Filled in at emission; a report constructed with it set keeps it.
    """

    model: str
    usage: Usage
    batch: bool = False
    requests: int = 1
    tenant_id: str | None = None


UsageSink = Callable[[UsageReport], "None | Awaitable[None]"]
_usage_sinks: list[UsageSink] = []


def register_usage_sink(sink: UsageSink) -> None:
    """Subscribe *sink* to every billed turn. Idempotent."""
    if sink not in _usage_sinks:
        _usage_sinks.append(sink)


def unregister_usage_sink(sink: UsageSink) -> None:
    """Remove a previously registered sink (no-op when absent)."""
    if sink in _usage_sinks:
        _usage_sinks.remove(sink)


def _sink_name(sink: UsageSink) -> str:
    """A low-cardinality label for *sink* (its name, never its repr/address)."""
    return str(getattr(sink, "__qualname__", None) or type(sink).__name__)


def _sink_failed(sink: UsageSink, exc: BaseException) -> None:
    from core.observability.metrics import USAGE_SINK_FAILURES_TOTAL

    name = _sink_name(sink)
    USAGE_SINK_FAILURES_TOTAL.labels(sink=name).inc()
    logger.warning("usage sink failed", sink=name, error=f"{type(exc).__name__}: {exc}")


def _schedule(sink: UsageSink, coro: Awaitable[None]) -> None:
    """Run an async sink's coroutine without holding the caller.

    On a running loop it is scheduled as a task whose failure is reported
    like a sync sink's; with no loop (a sync script) it runs to completion
    here, which is the only loop there is.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:

        async def _run() -> None:
            await coro

        try:
            asyncio.run(_run())
        except Exception as exc:
            _sink_failed(sink, exc)
        return

    future: asyncio.Future[None] = asyncio.ensure_future(coro, loop=loop)

    def _done(done: asyncio.Future[None]) -> None:
        if done.cancelled():
            return
        exc = done.exception()
        if exc is not None:
            _sink_failed(sink, exc)

    future.add_done_callback(_done)


def emit_usage_report(report: UsageReport) -> None:
    """Deliver *report* to every registered sink. Never raises.

    An empty record is not delivered: a cache hit or a call the provider
    never answered moved no billed tokens. The bound tenant, if any, is
    stamped on the report before delivery.
    """
    if report.usage.is_empty:
        return
    if report.tenant_id is None and tenant_is_bound():
        report = replace(report, tenant_id=get_current_tenant_id())
    for sink in list(_usage_sinks):
        try:
            result = sink(report)
        except Exception as exc:
            _sink_failed(sink, exc)
            continue
        if inspect.isawaitable(result):
            _schedule(sink, result)
