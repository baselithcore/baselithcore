"""Unit tests for pluggable secret resolution."""

import importlib.metadata

import pytest
from pydantic import SecretStr

from core.security.secrets import (
    ENTRY_POINT_GROUP,
    EnvSecretsProvider,
    FileSecretsProvider,
    SecretsProvider,
    get_secret,
    get_secrets_provider,
    register_secrets_provider,
    reset_secrets_provider,
)


@pytest.fixture(autouse=True)
def _reset_provider(monkeypatch):
    # The backend comes from the cached SecurityConfig: drop the cache so each
    # test's environment is read afresh.
    monkeypatch.setattr("core.config.security._security_config", None)
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

    def test_backend_comes_from_security_config(self, monkeypatch, tmp_path):
        """SecurityConfig is the source, not a second raw os.environ read."""
        from types import SimpleNamespace

        monkeypatch.delenv("SECRETS_BACKEND", raising=False)
        monkeypatch.setattr(
            "core.config.security._security_config",
            SimpleNamespace(secrets_backend="file", secrets_dir=str(tmp_path)),
        )
        provider = get_secrets_provider()
        assert isinstance(provider, FileSecretsProvider)

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


class _FakeDist:
    def __init__(self, name):
        self.name = name


class _FakeEntryPoint:
    """Stands in for ``importlib.metadata.EntryPoint`` (name/value/dist/load)."""

    def __init__(self, name, target, dist="acme-secrets"):
        self.name = name
        self.value = f"acme_secrets:{name}"
        self.dist = _FakeDist(dist)
        self._target = target

    def load(self):
        if isinstance(self._target, BaseException):
            raise self._target
        return self._target


class _StaticProvider:
    def __init__(self, value="from-entry-point"):
        self._value = value

    def get_secret(self, name):
        return SecretStr(self._value)


class TestEntryPointDiscovery:
    @pytest.fixture(autouse=True)
    def _isolate_registry(self, monkeypatch):
        # Earlier tests register backends into the module-level dict; keep this
        # class independent of test order (pytest-randomly).
        monkeypatch.setattr("core.security.secrets._PROVIDER_FACTORIES", {})

    @pytest.fixture
    def entry_points(self, monkeypatch):
        """Install fake entry points; record every group that was scanned."""
        installed: list[_FakeEntryPoint] = []
        calls: list[str] = []

        def fake_entry_points(*, group):
            calls.append(group)
            return list(installed) if group == ENTRY_POINT_GROUP else []

        monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
        return installed, calls

    def test_discovered_backend_is_built(self, monkeypatch, entry_points):
        installed, calls = entry_points
        installed.append(_FakeEntryPoint("vault", _StaticProvider))
        monkeypatch.setenv("SECRETS_BACKEND", "Vault")  # case-insensitive
        provider = get_secrets_provider()
        assert isinstance(provider, _StaticProvider)
        assert provider.get_secret("X").get_secret_value() == "from-entry-point"
        assert calls == [ENTRY_POINT_GROUP]

    def test_explicit_registration_wins(self, monkeypatch, entry_points):
        installed, calls = entry_points
        installed.append(_FakeEntryPoint("vault", _StaticProvider))
        register_secrets_provider("vault", lambda: _StaticProvider("registered"))
        monkeypatch.setenv("SECRETS_BACKEND", "vault")
        assert get_secrets_provider().get_secret("X").get_secret_value() == (
            "registered"
        )
        assert calls == []  # registration short-circuits discovery

    @pytest.mark.parametrize("builtin", ["env", "file", "ENV"])
    def test_builtins_cannot_be_registered(self, builtin):
        with pytest.raises(ValueError, match="built-in"):
            register_secrets_provider(builtin, _StaticProvider)

    @pytest.mark.parametrize(
        ("backend", "expected"),
        [("env", EnvSecretsProvider), ("file", FileSecretsProvider)],
    )
    def test_builtins_ignore_entry_points_and_skip_discovery(
        self, monkeypatch, entry_points, backend, expected
    ):
        installed, calls = entry_points
        installed.append(_FakeEntryPoint(backend, _StaticProvider))
        monkeypatch.setenv("SECRETS_BACKEND", backend)
        assert isinstance(get_secrets_provider(), expected)
        assert calls == []  # the default path never scans package metadata

    def test_load_failure_raises_with_name(self, monkeypatch, entry_points):
        installed, _ = entry_points
        installed.append(
            _FakeEntryPoint("vault", ImportError("token=s.leaky-value missing"))
        )
        monkeypatch.setenv("SECRETS_BACKEND", "vault")
        with pytest.raises(RuntimeError, match="'vault'") as info:
            get_secrets_provider()
        assert "ImportError" in str(info.value)
        assert "leaky-value" not in str(info.value)  # no error text echoed
        assert isinstance(info.value.__cause__, ImportError)

    def test_factory_failure_raises(self, monkeypatch, entry_points):
        def boom():
            raise ConnectionError("vault unreachable")

        installed, _ = entry_points
        installed.append(_FakeEntryPoint("vault", boom))
        monkeypatch.setenv("SECRETS_BACKEND", "vault")
        with pytest.raises(RuntimeError, match="failed to load"):
            get_secrets_provider()

    def test_bad_return_type_raises(self, monkeypatch, entry_points):
        installed, _ = entry_points
        installed.append(_FakeEntryPoint("vault", lambda: object()))
        monkeypatch.setenv("SECRETS_BACKEND", "vault")
        with pytest.raises(TypeError, match="'vault'.*does not implement"):
            get_secrets_provider()

    def test_registered_factory_bad_return_type_raises(self, monkeypatch):
        register_secrets_provider("broken", lambda: None)
        monkeypatch.setenv("SECRETS_BACKEND", "broken")
        with pytest.raises(TypeError, match="does not implement"):
            get_secrets_provider()

    def test_duplicate_names_raise(self, monkeypatch, entry_points):
        installed, _ = entry_points
        installed.append(_FakeEntryPoint("vault", _StaticProvider, dist="pkg-a"))
        installed.append(_FakeEntryPoint("VAULT", _StaticProvider, dist="pkg-b"))
        monkeypatch.setenv("SECRETS_BACKEND", "vault")
        with pytest.raises(ValueError, match="Ambiguous") as info:
            get_secrets_provider()
        assert "pkg-a" in str(info.value) and "pkg-b" in str(info.value)

    def test_unknown_backend_lists_discovered(self, monkeypatch, entry_points):
        installed, _ = entry_points
        installed.append(_FakeEntryPoint("aws-sm", _StaticProvider))
        installed.append(_FakeEntryPoint("gcp-sm", _StaticProvider))
        register_secrets_provider("local-test", _StaticProvider)
        monkeypatch.setenv("SECRETS_BACKEND", "vault")
        with pytest.raises(ValueError, match="Unknown secrets backend") as info:
            get_secrets_provider()
        message = str(info.value)
        assert "['env', 'file']" in message
        assert "['local-test']" in message
        assert "['aws-sm', 'gcp-sm']" in message

    def test_failure_is_not_cached(self, monkeypatch, entry_points):
        """A failed build leaves no provider behind, so nothing reads env."""
        installed, _ = entry_points
        installed.append(_FakeEntryPoint("vault", lambda: object()))
        monkeypatch.setenv("SECRETS_BACKEND", "vault")
        for _ in range(2):
            with pytest.raises(TypeError):
                get_secret("ANY")
