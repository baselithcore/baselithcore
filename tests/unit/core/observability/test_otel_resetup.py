"""setup_telemetry() after shutdown_telemetry() must really export again.

OpenTelemetry refuses to replace the global ``TracerProvider`` once set. The
bootstrap used to build a fresh provider on every setup and report success,
while every span kept flowing into the first, already shut-down provider. It
also registered another ``atexit`` hook on every setup.
"""

from __future__ import annotations

import pytest
from opentelemetry import metrics, trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from core.observability import otel
from core.observability.otel_exporters import PROTOCOL_GRPC

pytestmark = [pytest.mark.unit]


class _Cfg:
    telemetry_enabled = True
    telemetry_otel_endpoint = "http://collector:4317"
    telemetry_console_export = False
    telemetry_traces_sample_rate = 1.0
    telemetry_otel_protocol = "grpc"
    telemetry_metrics_enabled = False
    telemetry_logs_enabled = False


@pytest.fixture
def harness(monkeypatch):
    """Fresh process-level OTel state with exporters captured in memory."""
    for name, value in {
        "_initialized": False,
        "_tracer_provider": None,
        "_meter_provider": None,
        "_global_tracer_provider": None,
        "_span_pipeline": None,
        "_meter_provider_was_installed": False,
        "_atexit_registered": False,
    }.items():
        monkeypatch.setattr(otel, name, value)

    installed: list[object] = []
    exporters: list[InMemorySpanExporter] = []
    atexit_calls: list[object] = []

    def _exporter(endpoint: str, protocol: str) -> InMemorySpanExporter:
        exporter = InMemorySpanExporter()
        exporters.append(exporter)
        return exporter

    monkeypatch.setattr(otel, "get_app_config", lambda: _Cfg())
    monkeypatch.setattr(otel, "build_span_exporter", _exporter)
    monkeypatch.setattr(otel, "setup_propagators", lambda: None)
    monkeypatch.setattr(otel, "instrument_libraries", lambda *a, **k: None)
    monkeypatch.setattr(trace, "set_tracer_provider", installed.append)
    monkeypatch.setattr(otel.atexit, "register", atexit_calls.append)

    yield installed, exporters, atexit_calls
    otel.shutdown_telemetry()


def _emit(provider, name: str) -> None:
    with provider.get_tracer("t").start_as_current_span(name):
        pass
    provider.force_flush()


class TestResetup:
    def test_second_setup_exports_through_the_first_provider(self, harness):
        installed, exporters, _ = harness

        assert otel.setup_telemetry() is True
        provider = otel._global_tracer_provider
        _emit(provider, "first")
        otel.shutdown_telemetry()
        assert otel.is_initialized() is False

        assert otel.setup_telemetry() is True
        assert otel.is_initialized() is True
        _emit(provider, "second")

        # The global provider was installed once and never replaced.
        assert installed == [provider]
        assert [s.name for s in exporters[0].get_finished_spans()] == ["first"]
        assert [s.name for s in exporters[1].get_finished_spans()] == ["second"]

    def test_shutdown_closes_the_previous_exporters(self, harness):
        _, exporters, _ = harness

        otel.setup_telemetry()
        otel.shutdown_telemetry()
        otel.setup_telemetry()
        _emit(otel._global_tracer_provider, "late")

        assert exporters[0].get_finished_spans() == ()

    def test_atexit_is_registered_once(self, harness):
        _, _, atexit_calls = harness

        for _ in range(3):
            otel.setup_telemetry()
            otel.shutdown_telemetry()

        # The SDK's own provider registers a bound-method hook; count ours.
        assert atexit_calls.count(otel.shutdown_telemetry) == 1


class TestMetricsResetup:
    def test_second_meter_provider_is_refused_with_a_warning(self, monkeypatch):
        monkeypatch.setattr(otel, "_meter_provider_was_installed", False)
        installed: list[object] = []
        monkeypatch.setattr(metrics, "set_meter_provider", installed.append)

        first = otel._setup_metrics(None, None, PROTOCOL_GRPC, True)
        assert first is not None
        first.shutdown()

        assert otel._setup_metrics(None, None, PROTOCOL_GRPC, True) is None
        assert installed == [first]
