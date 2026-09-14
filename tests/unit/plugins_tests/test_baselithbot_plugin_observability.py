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


def test_energy_threshold_wake_creates_callable() -> None:
    from plugins.baselithbot.voice import (
        EnergyThresholdWake,
        SoundDeviceAudioBackend,
    )

    backend = SoundDeviceAudioBackend()
    wake = EnergyThresholdWake(backend, threshold_rms=1500.0)
    fn = wake.make_async_callable()
    assert callable(fn)


# --- /metrics exposition negotiation ----------------------------------------
#
# Same defect the core /metrics router carried: this exporter shares the default
# prometheus_client registry, so the trace_id exemplars that
# core.observability.metric_context attaches are present in it — and returning
# the Prometheus text format unconditionally dropped every one of them. It also
# labelled 0.0.4 bytes as `version=1.0.0`.

BOT_OPENMETRICS_ACCEPT = (
    "application/openmetrics-text;version=1.0.0;q=0.75,"
    "text/plain;version=0.0.4;q=0.5,*/*;q=0.1"
)


def test_metrics_render_returns_payload() -> None:
    from plugins.baselithbot.observability.metrics import (
        is_prometheus_available,
        render_metrics,
    )

    payload, content_type = render_metrics()
    assert isinstance(payload, bytes)
    assert content_type
    assert isinstance(is_prometheus_available(), bool)


def test_metrics_render_serves_openmetrics_with_exemplars() -> None:
    """An exemplar recorded on the shared registry must reach the body."""
    prom = pytest.importorskip("prometheus_client")

    from plugins.baselithbot.observability import metrics as metrics_module

    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    registry = prom.CollectorRegistry()
    histogram = prom.Histogram(
        "bot_demo_latency_seconds",
        "Demo latency.",
        registry=registry,
        buckets=(0.1, float("inf")),
    )
    histogram.observe(0.05, exemplar={"trace_id": trace_id})

    payload, content_type = metrics_module.render_metrics(
        BOT_OPENMETRICS_ACCEPT, registry=registry
    )

    assert content_type.startswith("application/openmetrics-text")
    body = payload.decode()
    assert f'trace_id="{trace_id}"' in body
    # OpenMetrics is a terminated format; Prometheus rejects a body without it.
    assert body.endswith("# EOF\n")


def test_metrics_render_defaults_to_plain_text() -> None:
    """No Accept header — the diagnostics passthrough — stays on text/plain.

    That caller decodes the payload into a JSON string field for display, so it
    must never be handed the OpenMetrics encoding.
    """
    pytest.importorskip("prometheus_client")

    from plugins.baselithbot.observability.metrics import render_metrics

    payload, content_type = render_metrics()

    assert content_type.startswith("text/plain")
    assert "# EOF" not in payload.decode()


def test_metrics_render_labels_the_plain_fallback_honestly() -> None:
    """0.0.4 bytes must not be labelled version=1.0.0."""
    pytest.importorskip("prometheus_client")

    from plugins.baselithbot.observability.metrics import render_metrics

    assert render_metrics("*/*")[1] == "text/plain; version=0.0.4; charset=utf-8"


def test_metrics_render_never_claims_openmetrics_without_prometheus() -> None:
    """Without prometheus_client the stub must not advertise what it cannot emit.

    Negotiation added a `choose_encoder` stub to the ImportError branch. If it
    echoed the requested OpenMetrics content type it would hand a scraper a
    plain-text note labelled `application/openmetrics-text`, which fails the
    scrape on a parse error instead of degrading to "no data here".

    The module's only third-party import is prometheus_client and it has no
    intra-package imports, so its source is exec'd in a clean namespace with
    that one import blocked — the rest of the plugin is not involved.
    """
    import builtins
    import pathlib

    src = pathlib.Path("plugins/baselithbot/observability/metrics.py").read_text(
        encoding="utf-8"
    )
    real_import = builtins.__import__

    def blocked(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("prometheus_client"):
            raise ImportError("simulated: prometheus_client absent")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    namespace: dict[str, object] = {"__name__": "metrics_fallback_probe"}
    builtins.__import__ = blocked  # type: ignore[assignment]
    try:
        exec(compile(src, "metrics.py", "exec"), namespace)
    finally:
        builtins.__import__ = real_import

    render = namespace["render_metrics"]
    assert namespace["is_prometheus_available"]() is False  # type: ignore[operator]
    for accept in ("", "application/openmetrics-text;version=1.0.0"):
        payload, content_type = render(accept)  # type: ignore[operator]
        assert content_type == "text/plain"
        assert b"not installed" in payload
