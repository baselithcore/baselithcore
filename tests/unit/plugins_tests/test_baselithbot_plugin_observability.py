"""Unit tests for the Baselithbot plugin — usage ledger, model routing, telemetry."""

from __future__ import annotations

import pytest


def test_usage_ledger_summary_and_breakdown(tmp_path) -> None:
    from plugins.baselithbot.observability.usage import UsageEvent, UsageLedger

    ledger = UsageLedger(ledger_path=str(tmp_path / "usage.jsonl"))
    ledger.record(
        UsageEvent(
            session_id="s1",
            agent_id="a1",
            channel="webchat",
            model="opus-4.7",
            prompt_tokens=100,
            completion_tokens=200,
            cost_usd=0.05,
            latency_ms=120,
        )
    )
    ledger.record(
        UsageEvent(
            session_id="s1",
            agent_id="a1",
            channel="webchat",
            model="opus-4.7",
            prompt_tokens=50,
            completion_tokens=80,
            cost_usd=0.02,
            latency_ms=80,
        )
    )
    summary = ledger.summary()
    assert summary["total_tokens"] == 430
    assert summary["total_cost_usd"] == 0.07
    by_session = ledger.by_session("s1")
    assert by_session["events"] == 2
    breakdown = ledger.by_model_breakdown()
    assert breakdown["opus-4.7"]["events"] == 2


@pytest.mark.asyncio
async def test_failover_policy_skips_failed_provider() -> None:
    from plugins.baselithbot.model_routing import (
        FailoverPolicy,
        ProviderConfig,
        ProviderError,
    )

    p = FailoverPolicy(
        [
            ProviderConfig(name="primary", model="x", cooldown_seconds=0.1),
            ProviderConfig(name="secondary", model="y"),
        ]
    )

    calls: list[str] = []

    async def action(provider):
        calls.append(provider.name)
        if provider.name == "primary":
            raise ProviderError("boom")
        return {"ok": provider.name}

    out = await p.call(action)
    assert out["provider"] == "secondary"
    assert calls == ["primary", "secondary"]


def test_auth_profile_pool_round_robin() -> None:
    from plugins.baselithbot.model_routing import AuthProfile, AuthProfilePool

    pool = AuthProfilePool(
        [
            AuthProfile(name="p1", api_key="k1"),
            AuthProfile(name="p2", api_key="k2"),
        ]
    )
    picks = [pool.acquire().name for _ in range(4)]
    assert picks == ["p1", "p2", "p1", "p2"]


@pytest.mark.asyncio
async def test_measure_usage_records_event() -> None:
    from plugins.baselithbot.observability.hooks import measure_usage
    from plugins.baselithbot.observability.usage import UsageLedger

    ledger = UsageLedger()
    async with measure_usage(ledger, agent_id="x", model="opus") as info:
        info["prompt_tokens"] = 7
        info["completion_tokens"] = 11
        info["cost_usd"] = 0.001
    summary = ledger.summary()
    assert summary["total_tokens"] == 18
    assert summary["events_in_buffer"] == 1


def test_trace_span_noop_or_real() -> None:
    from plugins.baselithbot.observability.tracing import is_tracing_enabled, trace_span

    with trace_span("baselithbot.test", foo="bar"):
        pass
    assert isinstance(is_tracing_enabled(), bool)


def test_metrics_render_returns_payload() -> None:
    from plugins.baselithbot.observability.metrics import (
        is_prometheus_available,
        render_metrics,
    )

    payload, content_type = render_metrics()
    assert isinstance(payload, bytes)
    assert content_type
    assert isinstance(is_prometheus_available(), bool)


def test_energy_threshold_wake_creates_callable() -> None:
    from plugins.baselithbot.voice import (
        EnergyThresholdWake,
        SoundDeviceAudioBackend,
    )

    backend = SoundDeviceAudioBackend()
    wake = EnergyThresholdWake(backend, threshold_rms=1500.0)
    fn = wake.make_async_callable()
    assert callable(fn)
