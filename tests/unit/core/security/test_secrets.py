"""Unit tests for pluggable secret resolution."""

import importlib.metadata

import pytest
from pydantic import SecretStr

from baselith.core.security.secrets import (
    ENTRY_POINT_GROUP,
    EnvSecretsProvider,
    FileSecretsProvider,
    SecretsProvider,
    get_secret,
    get_secrets_provider,
    register_secrets_provider,
    reset_secrets_provider,
    set_secret_guard,
)


@pytest.fixture(autouse=True)
def _reset_provider(monkeypatch):
    # The backend comes from the cached SecurityConfig: drop the cache so each
    # test's environment is read afresh.
    monkeypatch.setattr("baselith.core.config.security._security_config", None)
    reset_secrets_provider()
    yield
    reset_secrets_provider()
    set_secret_guard(None)


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
            "baselith.core.config.security._security_config",
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
        monkeypatch.setattr("baselith.core.security.secrets._PROVIDER_FACTORIES", {})

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

    def test_duplicate_origin_falls_back_to_entry_point_value(
        self, monkeypatch, entry_points
    ):
        """An entry point without distribution metadata still gets named."""
        installed, _ = entry_points
        dist_none = _FakeEntryPoint("vault", _StaticProvider)
        dist_none.dist = None
        no_dist_attr = _FakeEntryPoint("vault", _StaticProvider)
        del no_dist_attr.dist
        installed.extend([dist_none, no_dist_attr])
        monkeypatch.setenv("SECRETS_BACKEND", "vault")
        with pytest.raises(ValueError, match="Ambiguous") as info:
            get_secrets_provider()
        assert "(acme_secrets:vault, acme_secrets:vault)" in str(info.value)

    def test_registered_bad_return_names_backend_and_type(self, monkeypatch):
        register_secrets_provider("broken", lambda: 42)
        monkeypatch.setenv("SECRETS_BACKEND", "broken")
        with pytest.raises(TypeError, match="registered backend 'broken' returned int"):
            get_secrets_provider()


class TestFileProviderContract:
    def test_env_fallback_is_on_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ONLY_ENV", "env-val")
        assert FileSecretsProvider(tmp_path).get_secret(
            "ONLY_ENV"
        ).get_secret_value() == ("env-val")

    @pytest.mark.parametrize(
        ("content", "secret"),
        [(" pa ss \t\n", " pa ss \t"), ("tokenXX\n", "tokenXX")],
    )
    def test_only_trailing_newlines_are_stripped(self, tmp_path, content, secret):
        # Whitespace and any other character are part of the secret.
        (tmp_path / "PW").write_text(content)
        assert FileSecretsProvider(tmp_path).get_secret("PW").get_secret_value() == (
            secret
        )

    def test_reads_utf8(self, tmp_path):
        (tmp_path / "PW").write_bytes("pässwörd€".encode())
        assert FileSecretsProvider(tmp_path).get_secret("PW").get_secret_value() == (
            "pässwörd€"
        )

    def test_precedence_dir_then_file_var_then_env(self, tmp_path, monkeypatch):
        external = tmp_path / "external"
        external.write_text("from-file-var")
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir()
        monkeypatch.setenv("PREC", "from-env")
        monkeypatch.setenv("PREC_FILE", str(external))
        provider = FileSecretsProvider(secrets_dir)
        assert provider.get_secret("PREC").get_secret_value() == "from-file-var"
        (secrets_dir / "PREC").write_text("from-dir")
        assert provider.get_secret("PREC").get_secret_value() == "from-dir"

    @pytest.mark.parametrize(
        ("exc", "reason"),
        [
            (PermissionError(13, "Permission denied"), "Permission denied"),
            (OSError(), "OSError"),
        ],
    )
    def test_unreadable_file_logs_reason_not_path(
        self, tmp_path, monkeypatch, caplog, exc, reason
    ):
        (tmp_path / "DB_PASSWORD").write_text("x")

        def deny(self, *args, **kwargs):
            raise exc

        monkeypatch.setattr("pathlib.Path.read_text", deny)
        provider = FileSecretsProvider(tmp_path, fallback_env=False)
        with caplog.at_level("WARNING", logger="baselith.core.security.secrets"):
            assert provider.get_secret("DB_PASSWORD") is None
        messages = [
            r.getMessage()
            for r in caplog.records
            if r.name == "baselith.core.security.secrets"
        ]
        assert messages
        assert all(reason in m for m in messages)
        assert not any(str(tmp_path) in m or "DB_PASSWORD" in m for m in messages)


class TestProviderSelection:
    def test_file_backend_reads_the_configured_dir(self, monkeypatch, tmp_path):
        (tmp_path / "DB_PASSWORD").write_text("from-secrets-dir\n")
        monkeypatch.delenv("DB_PASSWORD", raising=False)
        monkeypatch.delenv("DB_PASSWORD_FILE", raising=False)
        monkeypatch.setenv("SECRETS_BACKEND", "file")
        monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
        assert get_secret("DB_PASSWORD").get_secret_value() == "from-secrets-dir"

    def test_logs_the_provider_class_only(self, monkeypatch, caplog):
        monkeypatch.delenv("SECRETS_BACKEND", raising=False)
        with caplog.at_level("INFO", logger="baselith.core.security.secrets"):
            get_secrets_provider()
        assert [
            r.getMessage()
            for r in caplog.records
            if r.name == "baselith.core.security.secrets"
        ] == ["Initialized provider: EnvSecretsProvider"]


class TestSecretGuard:
    def test_guard_sees_every_name_and_can_refuse(self, monkeypatch):
        monkeypatch.delenv("SECRETS_BACKEND", raising=False)
        monkeypatch.setenv("ALLOWED", "ok-value")
        monkeypatch.setenv("FORBIDDEN", "must-not-leak")
        seen: list[str] = []

        def guard(name: str) -> None:
            seen.append(name)
            if name == "FORBIDDEN":
                raise PermissionError(name)

        set_secret_guard(guard)
        assert get_secret("ALLOWED").get_secret_value() == "ok-value"
        with pytest.raises(PermissionError):
            get_secret("FORBIDDEN")
        assert seen == ["ALLOWED", "FORBIDDEN"]

    def test_clearing_the_guard_restores_reads(self, monkeypatch):
        monkeypatch.delenv("SECRETS_BACKEND", raising=False)
        monkeypatch.setenv("FORBIDDEN", "v")

        def deny(name: str) -> None:
            raise PermissionError(name)

        set_secret_guard(deny)
        with pytest.raises(PermissionError):
            get_secret("FORBIDDEN")
        set_secret_guard(None)
        assert get_secret("FORBIDDEN").get_secret_value() == "v"
