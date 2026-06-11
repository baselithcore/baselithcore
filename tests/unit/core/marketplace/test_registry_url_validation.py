"""Tests for marketplace registry URL scheme enforcement."""

import pytest

from core.marketplace.registry import PluginRegistry


@pytest.fixture(autouse=True)
def _no_http_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BASELITH_MARKETPLACE_ALLOW_HTTP", raising=False)


def test_https_allowed() -> None:
    PluginRegistry._validate_registry_url(
        "https://marketplace.example.com/registry.json"
    )


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_http_loopback_allowed(host: str) -> None:
    PluginRegistry._validate_registry_url(f"http://{host}:8080/registry.json")


def test_http_remote_rejected() -> None:
    with pytest.raises(ValueError, match="Refusing plaintext HTTP"):
        PluginRegistry._validate_registry_url("http://registry.example.com/r.json")


def test_http_remote_allowed_with_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BASELITH_MARKETPLACE_ALLOW_HTTP", "true")
    PluginRegistry._validate_registry_url("http://registry.example.com/r.json")


@pytest.mark.parametrize("url", ["ftp://x/r.json", "gopher://x/r", "registry.json"])
def test_other_schemes_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="Unsupported marketplace registry scheme"):
        PluginRegistry._validate_registry_url(url)
