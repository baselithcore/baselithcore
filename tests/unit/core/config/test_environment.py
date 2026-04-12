from core.config.environment import get_runtime_environment, is_production_env


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
