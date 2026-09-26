"""TELEMETRY_TRACES_SAMPLE_RATE resolves its default from the environment.

A flat 1.0 default exported every span tree of every production request to
the collector; unset, production now samples one trace in ten.
"""

import pytest

from core.config.app import PRODUCTION_TRACES_SAMPLE_RATE, AppConfig


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_ENV", "ENVIRONMENT", "TELEMETRY_TRACES_SAMPLE_RATE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("env", ["development", "test", "staging"])
def test_non_production_traces_everything(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("APP_ENV", env)

    assert AppConfig().telemetry_traces_sample_rate == 1.0


@pytest.mark.parametrize("env", ["production", "prod", "some-unknown-env"])
def test_production_samples_conservatively(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    monkeypatch.setenv("APP_ENV", env)

    assert AppConfig().telemetry_traces_sample_rate == PRODUCTION_TRACES_SAMPLE_RATE
    assert PRODUCTION_TRACES_SAMPLE_RATE == 0.1


def test_explicit_value_wins_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("TELEMETRY_TRACES_SAMPLE_RATE", "1.0")

    assert AppConfig().telemetry_traces_sample_rate == 1.0


def test_out_of_range_value_still_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_TRACES_SAMPLE_RATE", "1.5")

    with pytest.raises(ValueError):
        AppConfig()
