"""Probe/scrape URLs are excluded from FastAPI auto-instrumentation."""

from __future__ import annotations

import sys
import types

from core.observability import otel


def test_default_exclusions_are_anchored_probe_paths(monkeypatch):
    monkeypatch.delenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", raising=False)
    monkeypatch.delenv("OTEL_PYTHON_EXCLUDED_URLS", raising=False)
    value = otel._fastapi_excluded_urls()
    assert value is not None
    patterns = value.split(",")
    assert "/health$" in patterns
    assert "/health/ready$" in patterns
    assert "/metrics$" in patterns
    # Anchored: a route that merely contains the word must stay traced.
    assert all(p.endswith("$") for p in patterns)


def test_operator_env_wins_over_default(monkeypatch):
    monkeypatch.setenv("OTEL_PYTHON_EXCLUDED_URLS", "/custom")
    assert otel._fastapi_excluded_urls() is None
    monkeypatch.delenv("OTEL_PYTHON_EXCLUDED_URLS")
    monkeypatch.setenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "/custom")
    assert otel._fastapi_excluded_urls() is None


def test_try_instrument_forwards_kwargs_and_drops_none(monkeypatch):
    seen: dict = {}

    class _Instrumentor:
        def instrument(self, **kwargs):
            seen.update(kwargs)

    fake = types.ModuleType("fake_otel_instrumentation")
    fake.Instr = _Instrumentor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_otel_instrumentation", fake)

    otel._try_instrument(
        "fake_otel_instrumentation",
        "Instr",
        "Fake",
        excluded_urls="/health$",
        other=None,
    )
    assert seen == {"excluded_urls": "/health$"}
