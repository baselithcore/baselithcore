"""``OTEL_TRACES_SAMPLER`` / ``OTEL_TRACES_SAMPLER_ARG`` support.

The SDK reads these two variables when a ``TracerProvider`` is built without an
explicit sampler; this framework always passes one, which silently overrode
them. These tests pin the documented spellings.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_OFF,
    ALWAYS_ON,
    ParentBased,
    TraceIdRatioBased,
)

from core.observability import otel

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _no_sampler_env(monkeypatch):
    monkeypatch.delenv("OTEL_TRACES_SAMPLER", raising=False)
    monkeypatch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)


def _set(monkeypatch, name: str, arg: str | None = None) -> None:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", name)
    if arg is not None:
        monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", arg)


class TestSamplerFromEnvironment:
    def test_always_on(self, monkeypatch):
        _set(monkeypatch, "always_on")
        assert otel._build_sampler(0.01) is ALWAYS_ON

    def test_always_off(self, monkeypatch):
        _set(monkeypatch, "always_off")
        assert otel._build_sampler(1.0) is ALWAYS_OFF

    def test_traceidratio_uses_the_arg(self, monkeypatch):
        _set(monkeypatch, "traceidratio", "0.25")
        sampler = otel._build_sampler(1.0)
        assert isinstance(sampler, TraceIdRatioBased)
        assert sampler.rate == pytest.approx(0.25)

    def test_parentbased_always_on(self, monkeypatch):
        _set(monkeypatch, "parentbased_always_on")
        sampler = otel._build_sampler(0.01)
        assert isinstance(sampler, ParentBased)
        assert sampler._root is ALWAYS_ON

    def test_parentbased_always_off(self, monkeypatch):
        _set(monkeypatch, "parentbased_always_off")
        sampler = otel._build_sampler(1.0)
        assert isinstance(sampler, ParentBased)
        assert sampler._root is ALWAYS_OFF

    def test_parentbased_traceidratio(self, monkeypatch):
        _set(monkeypatch, "parentbased_traceidratio", "0.1")
        sampler = otel._build_sampler(1.0)
        assert isinstance(sampler, ParentBased)
        assert isinstance(sampler._root, TraceIdRatioBased)
        assert sampler._root.rate == pytest.approx(0.1)

    def test_name_is_case_and_whitespace_insensitive(self, monkeypatch):
        _set(monkeypatch, "  ALWAYS_ON ")
        assert otel._build_sampler(0.5) is ALWAYS_ON

    def test_ratio_defaults_to_one_without_an_arg(self, monkeypatch):
        _set(monkeypatch, "traceidratio")
        sampler = otel._build_sampler(0.01)
        assert isinstance(sampler, TraceIdRatioBased)
        assert sampler.rate == pytest.approx(1.0)

    def test_unparsable_arg_falls_back_to_one(self, monkeypatch):
        _set(monkeypatch, "traceidratio", "not-a-number")
        sampler = otel._build_sampler(0.01)
        assert isinstance(sampler, TraceIdRatioBased)
        assert sampler.rate == pytest.approx(1.0)

    def test_arg_is_clamped_into_range(self, monkeypatch):
        _set(monkeypatch, "traceidratio", "17")
        assert otel._build_sampler(1.0).rate == pytest.approx(1.0)
        _set(monkeypatch, "traceidratio", "-3")
        assert otel._build_sampler(1.0).rate == pytest.approx(0.0)

    def test_unknown_name_falls_back_to_the_configured_rate(self, monkeypatch):
        _set(monkeypatch, "jaeger_remote")
        sampler = otel._build_sampler(0.25)
        assert isinstance(sampler, ParentBased)
        assert isinstance(sampler._root, TraceIdRatioBased)
        assert sampler._root.rate == pytest.approx(0.25)

    def test_blank_value_falls_back_to_the_configured_rate(self, monkeypatch):
        _set(monkeypatch, "   ")
        assert otel._build_sampler(1.0) is not ALWAYS_ON
        assert isinstance(otel._build_sampler(1.0), ParentBased)


class TestConfiguredRateUnchanged:
    """The pre-existing behaviour must survive with no env set."""

    def test_full_rate_is_parentbased_always_on(self):
        sampler = otel._build_sampler(1.0)
        assert isinstance(sampler, ParentBased)
        assert sampler._root is ALWAYS_ON

    def test_partial_rate_is_parentbased_ratio(self):
        sampler = otel._build_sampler(0.25)
        assert isinstance(sampler, ParentBased)
        assert isinstance(sampler._root, TraceIdRatioBased)
        assert sampler._root.rate == pytest.approx(0.25)
