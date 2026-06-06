"""Unit tests for pluggable secret resolution."""

import pytest
from pydantic import SecretStr

from core.security.secrets import (
    EnvSecretsProvider,
    FileSecretsProvider,
    SecretsProvider,
    get_secrets_provider,
    register_secrets_provider,
    reset_secrets_provider,
)


@pytest.fixture(autouse=True)
def _reset_provider():
    reset_secrets_provider()
    yield
    reset_secrets_provider()


class TestEnvProvider:
    def test_reads_from_environment(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "value-123")
        provider = EnvSecretsProvider()
        result = provider.get_secret("MY_SECRET")
        assert isinstance(result, SecretStr)
        assert result.get_secret_value() == "value-123"

    def test_missing_returns_none(self, monkeypatch):
        monkeypatch.delenv("ABSENT", raising=False)
        assert EnvSecretsProvider().get_secret("ABSENT") is None


class TestFileProvider:
    def test_reads_from_secrets_dir(self, tmp_path):
        (tmp_path / "DB_PASSWORD").write_text("hunter2\n")
        provider = FileSecretsProvider(tmp_path)
        result = provider.get_secret("DB_PASSWORD")
        assert result is not None
        assert result.get_secret_value() == "hunter2"  # trailing newline stripped

    def test_lowercase_filename_fallback(self, tmp_path):
        (tmp_path / "api_key").write_text("k")
        assert (
            FileSecretsProvider(tmp_path).get_secret("API_KEY").get_secret_value()
            == "k"
        )

    def test_file_indirection_convention(self, tmp_path, monkeypatch):
        secret_file = tmp_path / "external.txt"
        secret_file.write_text("from-file")
        monkeypatch.setenv("TOKEN_FILE", str(secret_file))
        # No secrets dir; resolves via TOKEN_FILE.
        assert (
            FileSecretsProvider().get_secret("TOKEN").get_secret_value() == "from-file"
        )

    def test_env_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ONLY_ENV", "env-val")
        provider = FileSecretsProvider(tmp_path, fallback_env=True)
        assert provider.get_secret("ONLY_ENV").get_secret_value() == "env-val"

    def test_env_fallback_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ONLY_ENV", "env-val")
        provider = FileSecretsProvider(tmp_path, fallback_env=False)
        assert provider.get_secret("ONLY_ENV") is None

    def test_missing_returns_none(self, tmp_path):
        assert FileSecretsProvider(tmp_path, fallback_env=False).get_secret("X") is None


class TestFactoryAndRegistry:
    def test_default_backend_is_env(self, monkeypatch):
        monkeypatch.delenv("SECRETS_BACKEND", raising=False)
        assert isinstance(get_secrets_provider(), EnvSecretsProvider)

    def test_file_backend_selected(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SECRETS_BACKEND", "file")
        monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
        assert isinstance(get_secrets_provider(), FileSecretsProvider)

    def test_provider_is_cached(self, monkeypatch):
        monkeypatch.delenv("SECRETS_BACKEND", raising=False)
        assert get_secrets_provider() is get_secrets_provider()

    def test_unknown_backend_raises(self, monkeypatch):
        monkeypatch.setenv("SECRETS_BACKEND", "nope")
        with pytest.raises(ValueError, match="Unknown secrets backend"):
            get_secrets_provider()

    def test_register_custom_backend(self, monkeypatch):
        class DummyProvider:
            def get_secret(self, name):
                return SecretStr("dummy")

        register_secrets_provider("dummy", lambda: DummyProvider())
        monkeypatch.setenv("SECRETS_BACKEND", "dummy")
        provider = get_secrets_provider()
        assert isinstance(provider, SecretsProvider)
        assert provider.get_secret("anything").get_secret_value() == "dummy"
