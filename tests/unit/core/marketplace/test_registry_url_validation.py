"""Tests for marketplace registry URL scheme + SSRF enforcement."""

import socket

import pytest

from core.marketplace.registry import PluginRegistry


def _fake_getaddrinfo(mapping: dict[str, list[str]]):
    """Helper to fake socket.getaddrinfo with a host→[IPs] mapping."""

    def fake(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(f"unknown host {host}")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]
        ]

    return fake


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient opt-ins, and never touch real DNS.

    ``_validate_registry_url`` now runs the SSRF guard, which resolves the host.
    Pin resolution to a public IP so remote hostnames validate without network
    access; individual tests override this to simulate an internal target.
    """
    monkeypatch.delenv("BASELITH_MARKETPLACE_ALLOW_HTTP", raising=False)
    monkeypatch.delenv("BASELITH_MARKETPLACE_ALLOW_INTERNAL", raising=False)
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo({"marketplace.example.com": ["93.184.216.34"], "registry.example.com": ["93.184.216.34"], "internal-registry.corp": ["10.0.0.5"]})
    )


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


def test_https_internal_host_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """An https registry that resolves to cloud-metadata is refused (SSRF)."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        _fake_getaddrinfo({"evil.example.com": ["169.254.169.254"]}),
    )
    with pytest.raises(ValueError, match="SSRF guard"):
        PluginRegistry._validate_registry_url("https://evil.example.com/r.json")


def test_https_internal_allowed_with_optin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit opt-in permits an internal (on-prem/air-gapped) registry."""
    # Internal IP is already in the fixture mapping; test just needs the opt-in flag.
    monkeypatch.setenv("BASELITH_MARKETPLACE_ALLOW_INTERNAL", "true")
    PluginRegistry._validate_registry_url("https://internal-registry.corp/r.json")
