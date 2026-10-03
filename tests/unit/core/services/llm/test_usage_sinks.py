"""Tests for the billed-turn observer seam (`register_usage_sink`).

The token seam only carries ``(count, model)`` pairs — an estimated prompt,
no cache buckets, nothing for a batch job. These tests pin what the usage seam
adds: every turn booked on the tenant ledger reaches the sinks with its full
four-bucket record, batch flag and request count, and so does usage an
external engine reports.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from core.services.llm import (
    UsageReport,
    register_usage_sink,
    report_external_usage,
    unregister_usage_sink,
)
from core.services.llm.usage import Usage


def _bare(reports: list[UsageReport]) -> list[UsageReport]:
    """The reports without the tenant the test harness binds."""
    return [replace(r, tenant_id=None) for r in reports]


@pytest.fixture
def reports():
    seen: list[UsageReport] = []
    register_usage_sink(seen.append)
    try:
        yield seen
    finally:
        unregister_usage_sink(seen.append)


@pytest.fixture(autouse=True)
def _no_tenant_ledger(monkeypatch):
    async def _record(usd, **_kwargs):
        return None

    monkeypatch.setattr("core.quotas.cost_enforcement.record_tenant_llm_cost", _record)


async def test_booked_turn_reaches_sinks_with_every_bucket(reports) -> None:
    from core.services.llm._accounting import record_usage_cost

    usage = Usage(
        input_tokens=100, output_tokens=40, cache_read_tokens=900, cache_write_tokens=5
    )
    await record_usage_cost("gpt-4o-mini", usage)

    assert _bare(reports) == [UsageReport(model="gpt-4o-mini", usage=usage)]


async def test_batch_job_carries_its_rate_and_entry_count(reports) -> None:
    from core.services.llm._accounting import record_usage_cost

    usage = Usage(input_tokens=10, output_tokens=10)
    await record_usage_cost("gpt-4o-mini", usage, batch=True, requests=12)

    assert _bare(reports) == [
        UsageReport(model="gpt-4o-mini", usage=usage, batch=True, requests=12)
    ]


async def test_unpriced_model_is_observed_even_when_rejected(
    reports, monkeypatch
) -> None:
    # The reject policy means "don't meter the tenant ledger", not "the tokens
    # never happened": the sinks still see the turn and price it themselves.
    from core.services.llm._accounting import record_usage_cost

    monkeypatch.setenv("BASELITH_UNKNOWN_MODEL_COST_POLICY", "reject")
    await record_usage_cost("no-such-model", Usage(input_tokens=3, output_tokens=4))

    assert [r.model for r in reports] == ["no-such-model"]


async def test_empty_usage_is_not_delivered(reports) -> None:
    from core.services.llm._accounting import record_usage_cost

    await record_usage_cost("gpt-4o-mini", Usage())

    assert reports == []


def test_external_usage_reaches_sinks_as_one_turn(reports) -> None:
    report_external_usage("gpt-4o", prompt_tokens=70, completion_tokens=30)

    assert _bare(reports) == [
        UsageReport(model="gpt-4o", usage=Usage(input_tokens=70, output_tokens=30))
    ]


def test_a_failing_sink_neither_raises_nor_starves_the_next(reports) -> None:
    def broken(_report: UsageReport) -> None:
        raise RuntimeError("boom")

    unregister_usage_sink(reports.append)
    register_usage_sink(broken)
    register_usage_sink(reports.append)
    try:
        report_external_usage("gpt-4o", prompt_tokens=1, completion_tokens=1)
    finally:
        unregister_usage_sink(broken)

    assert len(reports) == 1


def test_register_is_idempotent(reports) -> None:
    register_usage_sink(reports.append)

    report_external_usage("gpt-4o", prompt_tokens=1, completion_tokens=1)

    assert len(reports) == 1


def test_a_failing_sink_is_logged_at_warning_and_counted(reports, monkeypatch) -> None:
    """A lost billing record must be visible in production logs, not DEBUG."""
    from core.observability.metrics import USAGE_SINK_FAILURES_TOTAL
    from core.services.llm import usage_sinks

    def broken(_report: UsageReport) -> None:
        raise RuntimeError("ledger down")

    warnings: list[tuple[str, dict]] = []

    class _Log:
        def warning(self, event: str, **kw) -> None:
            warnings.append((event, kw))

        def debug(self, *a, **kw) -> None:
            raise AssertionError("a lost ledger write must not be DEBUG")

    monkeypatch.setattr(usage_sinks, "logger", _Log())
    label = USAGE_SINK_FAILURES_TOTAL.labels(sink=broken.__qualname__)
    before = label._value.get()
    register_usage_sink(broken)
    try:
        report_external_usage("gpt-4o", prompt_tokens=1, completion_tokens=1)
    finally:
        unregister_usage_sink(broken)

    assert warnings and warnings[0][0] == "usage sink failed"
    assert "ledger down" in warnings[0][1]["error"]
    assert label._value.get() == before + 1


async def test_async_sink_is_awaited_off_the_caller(reports) -> None:
    seen: list[UsageReport] = []

    async def async_sink(report: UsageReport) -> None:
        await asyncio.sleep(0)
        seen.append(report)

    register_usage_sink(async_sink)
    try:
        report_external_usage("gpt-4o", prompt_tokens=1, completion_tokens=1)
        # Scheduled on the running loop, not run inline: give it a turn.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    finally:
        unregister_usage_sink(async_sink)

    assert len(seen) == 1


async def test_report_carries_the_bound_tenant(reports) -> None:
    from core.context import bind_principal_tenant, reset_tenant_context

    token = bind_principal_tenant("acme")
    try:
        report_external_usage("gpt-4o", prompt_tokens=1, completion_tokens=1)
    finally:
        reset_tenant_context(token)

    assert reports[0].tenant_id == "acme"


def test_report_without_a_bound_tenant_has_none(reports, monkeypatch) -> None:
    from core.services.llm import usage_sinks

    monkeypatch.setattr(usage_sinks, "tenant_is_bound", lambda: False)
    report_external_usage("gpt-4o", prompt_tokens=1, completion_tokens=1)

    assert reports[0].tenant_id is None
