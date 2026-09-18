"""Unit tests for the OTLP log-record bridge.

Three behaviours matter enough to pin:

* a provider is installed only when there is somewhere for records to go;
* the exporter's own transport chatter is never itself exported (the feedback
  loop that turns one failed batch into an unbounded one);
* shutdown detaches the handler from the root logger, because a handler left
  behind keeps queueing into a provider that has already flushed.
"""

from __future__ import annotations

import logging

import pytest
from opentelemetry.sdk.resources import Resource

from core.observability import otel_logs
from core.observability.otel_exporters import PROTOCOL_GRPC


@pytest.fixture(autouse=True)
def _clean_state():
    """Tear the module-global provider down around every test."""
    otel_logs.shutdown_log_export()
    yield
    otel_logs.shutdown_log_export()


@pytest.fixture
def resource():
    return Resource.create({"service.name": "test-service"})


def _root_otel_handlers():
    from opentelemetry.sdk._logs import LoggingHandler

    return [h for h in logging.getLogger().handlers if isinstance(h, LoggingHandler)]


class TestSetupLogExport:
    def test_no_destination_installs_nothing(self, resource):
        # No endpoint and no console: a provider here would be a batch thread
        # exporting to nowhere.
        assert (
            otel_logs.setup_log_export(
                resource, None, PROTOCOL_GRPC, console_export=False
            )
            is None
        )
        assert not otel_logs.is_active()
        assert _root_otel_handlers() == []

    def test_console_only_installs_a_provider(self, resource):
        provider = otel_logs.setup_log_export(
            resource, None, PROTOCOL_GRPC, console_export=True
        )
        assert provider is not None
        assert otel_logs.is_active()
        assert len(_root_otel_handlers()) == 1

    def test_endpoint_installs_a_provider(self, resource):
        # Constructing the exporter does not connect; nothing is sent here.
        provider = otel_logs.setup_log_export(
            resource, "http://localhost:4317", PROTOCOL_GRPC
        )
        assert provider is not None
        assert otel_logs.is_active()

    def test_second_call_is_a_no_op(self, resource):
        first = otel_logs.setup_log_export(
            resource, None, PROTOCOL_GRPC, console_export=True
        )
        second = otel_logs.setup_log_export(
            resource, None, PROTOCOL_GRPC, console_export=True
        )
        assert first is second
        assert len(_root_otel_handlers()) == 1


class TestShutdown:
    def test_shutdown_detaches_the_handler(self, resource):
        otel_logs.setup_log_export(resource, None, PROTOCOL_GRPC, console_export=True)
        assert len(_root_otel_handlers()) == 1

        otel_logs.shutdown_log_export()

        assert not otel_logs.is_active()
        assert _root_otel_handlers() == []

    def test_shutdown_without_setup_is_safe(self):
        otel_logs.shutdown_log_export()
        otel_logs.shutdown_log_export()
        assert not otel_logs.is_active()


class TestExporterLoopSuppression:
    """Records emitted by the export path must not be exported themselves."""

    @pytest.mark.parametrize(
        "name",
        [
            "opentelemetry.sdk._logs",
            "opentelemetry.exporter.otlp.proto.grpc",
            "urllib3.connectionpool",
            "grpc._channel",
            "core.observability.otel",
        ],
    )
    def test_export_path_loggers_are_dropped(self, name):
        record = logging.LogRecord(name, logging.WARNING, __file__, 1, "x", None, None)
        assert otel_logs._SuppressExporterLoops().filter(record) is False

    @pytest.mark.parametrize(
        "name",
        ["core.orchestration.orchestrator", "uvicorn.access", "plugins.baselithbot"],
    )
    def test_application_loggers_pass(self, name):
        record = logging.LogRecord(name, logging.INFO, __file__, 1, "x", None, None)
        assert otel_logs._SuppressExporterLoops().filter(record) is True
