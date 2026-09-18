"""Unit tests for OTLP protocol selection and per-signal endpoint shaping.

The endpoint assertions are the point of this module. gRPC and HTTP do not take
the same URL, and the SDK appends ``/v1/<signal>`` only for endpoints it reads
from the environment — an explicit ``endpoint=`` is used verbatim. Handing the
collector root to the HTTP exporter is therefore a silent 404 on every batch,
with no exception anywhere for a test to catch. These tests pin the shaping so
that regression cannot come back quietly.
"""

from __future__ import annotations

import pytest

from core.observability.otel_exporters import (
    PROTOCOL_GRPC,
    PROTOCOL_HTTP,
    build_log_exporter,
    build_metric_exporter,
    build_span_exporter,
    normalize_protocol,
    signal_endpoint,
)


class TestNormalizeProtocol:
    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_absent_value_defaults_to_grpc(self, value):
        assert normalize_protocol(value) == PROTOCOL_GRPC

    @pytest.mark.parametrize(
        "value",
        ["http/protobuf", "HTTP/PROTOBUF", "  http/protobuf  ", "http", "otlp-http"],
    )
    def test_http_spellings_resolve(self, value):
        assert normalize_protocol(value) == PROTOCOL_HTTP

    @pytest.mark.parametrize("value", ["grpc", "GRPC", "otlp-grpc"])
    def test_grpc_spellings_resolve(self, value):
        assert normalize_protocol(value) == PROTOCOL_GRPC

    def test_unknown_protocol_falls_back_and_warns(self, monkeypatch):
        # http/json is a valid value in the OTel spec but the Python SDK ships
        # no exporter for it: treated as unknown rather than silently ignored.
        warnings: list[tuple] = []
        monkeypatch.setattr(
            "core.observability.otel_exporters.logger.warning",
            lambda *args, **kwargs: warnings.append(args),
        )
        assert normalize_protocol("http/json") == PROTOCOL_GRPC
        assert warnings and "http/json" in warnings[0]


class TestSignalEndpoint:
    def test_grpc_endpoint_is_untouched(self):
        assert (
            signal_endpoint("http://otel:4317", PROTOCOL_GRPC, "traces")
            == "http://otel:4317"
        )

    @pytest.mark.parametrize(
        ("signal", "expected"),
        [
            ("traces", "http://otel:4318/v1/traces"),
            ("metrics", "http://otel:4318/v1/metrics"),
            ("logs", "http://otel:4318/v1/logs"),
        ],
    )
    def test_http_endpoint_gains_the_signal_path(self, signal, expected):
        assert signal_endpoint("http://otel:4318", PROTOCOL_HTTP, signal) == expected

    def test_trailing_slash_does_not_double_up(self):
        assert (
            signal_endpoint("http://otel:4318/", PROTOCOL_HTTP, "traces")
            == "http://otel:4318/v1/traces"
        )

    def test_explicit_signal_path_is_preserved(self):
        # An operator who already spelled the path out is not second-guessed.
        assert (
            signal_endpoint("http://otel:4318/v1/traces", PROTOCOL_HTTP, "traces")
            == "http://otel:4318/v1/traces"
        )


class TestExporterConstruction:
    """Each factory returns the exporter class matching the protocol asked for."""

    @pytest.mark.parametrize(
        ("factory", "signal"),
        [
            (build_span_exporter, "traces"),
            (build_metric_exporter, "metrics"),
            (build_log_exporter, "logs"),
        ],
    )
    def test_grpc_factories_build_grpc_exporters(self, factory, signal):
        exporter = factory("http://localhost:4317", PROTOCOL_GRPC)
        assert "proto.grpc" in type(exporter).__module__

    @pytest.mark.parametrize(
        ("factory", "signal"),
        [
            (build_span_exporter, "traces"),
            (build_metric_exporter, "metrics"),
            (build_log_exporter, "logs"),
        ],
    )
    def test_http_factories_build_http_exporters_on_the_signal_path(
        self, factory, signal
    ):
        exporter = factory("http://localhost:4318", PROTOCOL_HTTP)
        assert "proto.http" in type(exporter).__module__
        assert exporter._endpoint.endswith(f"/v1/{signal}")
