"""Tests for the billed-turn observer seam (`register_usage_sink`).

The token seam only carries ``(count, model)`` pairs — an estimated prompt,
no cache buckets, nothing for a batch job. These tests pin what the usage seam
adds: every turn booked on the tenant ledger reaches the sinks with its full
four-bucket record, batch flag and request count, and so does usage an
external engine reports.
"""

from __future__ import annotations

import pytest

from core.services.llm import (
    UsageReport,
    register_usage_sink,
    report_external_usage,
    unregister_usage_sink,
)
from core.services.llm.usage import Usage


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

    assert reports == [UsageReport(model="gpt-4o-mini", usage=usage)]


async def test_batch_job_carries_its_rate_and_entry_count(reports) -> None:
    from core.services.llm._accounting import record_usage_cost

    usage = Usage(input_tokens=10, output_tokens=10)
    await record_usage_cost("gpt-4o-mini", usage, batch=True, requests=12)

    assert reports == [
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

    assert reports == [
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
