import pytest

from core.config.environment import (
    get_runtime_environment,
    is_known_environment,
    is_production_env,
)


def test_runtime_environment_prefers_app_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ENVIRONMENT", "development")

    assert get_runtime_environment() == "production"
    assert is_production_env() is True


def test_runtime_environment_falls_back_to_environment(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "Production")

    assert get_runtime_environment() == "production"
    assert is_production_env() is True


@pytest.mark.parametrize("alias", ["prod", "PROD", " prd ", "live"])
def test_production_aliases_are_normalized(monkeypatch, alias):
    """Matching the literal 'production' meant APP_ENV=prod silently disabled
    plugin signature enforcement, unsigned-A2A rejection, the A2A SSRF deny,
    admin lockout, the JWT startup check and the /docs gate."""
    monkeypatch.setenv("APP_ENV", alias)

    assert get_runtime_environment() == "production"
    assert is_production_env() is True


@pytest.mark.parametrize(
    "name", ["development", "dev", "local", "test", "ci", "staging", "preprod"]
)
def test_known_non_production_environments_stay_permissive(monkeypatch, name):
    monkeypatch.setenv("APP_ENV", name)

    assert get_runtime_environment() == name
    assert is_production_env() is False


def test_unrecognised_environment_fails_closed(monkeypatch):
    """An environment we cannot classify gets the hardened posture rather than
    the permissive one."""
    monkeypatch.setenv("APP_ENV", "integration-eu")

    assert get_runtime_environment() == "integration-eu"
    assert is_known_environment("integration-eu") is False
    assert is_production_env() is True


def test_default_is_development(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    assert get_runtime_environment() == "development"
    assert is_production_env() is False


def test_a2a_and_integrity_share_one_definition(monkeypatch):
    """The two modules used to carry their own literal comparison, so they
    drifted from the shared helper the moment an alias was used."""
    from core.a2a import security as a2a_security
    from core.plugins import integrity

    monkeypatch.setenv("APP_ENV", "prod")

    assert integrity._is_production() is True
    assert a2a_security._is_production() is True
