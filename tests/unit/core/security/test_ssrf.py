"""Matrice SSRF condivisa per core.security.ssrf."""

from __future__ import annotations

import socket

import pytest

from core.security.ssrf import (
    SsrfError,
    SsrfPolicy,
    assert_url_safe,
    assert_url_safe_async,
    hostname_is_blocked_literal,
    ip_is_internal,
    resolve_pinned_target,
)


def _fake_getaddrinfo(mapping: dict[str, list[str]]):
    def fake(host, port, *args, **kwargs):
        if host not in mapping:
            raise socket.gaierror(f"unknown host {host}")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in mapping[host]
        ]

    return fake


class TestIpIsInternal:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fe80::1",
            "fc00::1",
            "::ffff:169.254.169.254",  # IPv4-mapped IPv6
            "::ffff:127.0.0.1",
            "not-an-ip",  # unparseable = fail-closed
            "100.64.0.1",  # RFC 6598 CGNAT
            "100.127.255.254",  # RFC 6598 CGNAT
            "192.88.99.1",  # Deprecated 6to4 relay anycast
        ],
    )
    def test_internal(self, ip):
        assert ip_is_internal(ip) is True

    @pytest.mark.parametrize(
        "ip",
        [
            "8.8.8.8",
            "1.1.1.1",
            "2606:4700::1111",
            "100.63.255.255",  # Outside CGNAT range
            "100.128.0.0",  # Outside CGNAT range
        ],
    )
    def test_public(self, ip):
        assert ip_is_internal(ip) is False


class TestLiteralCheck:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "LOCALHOST",
            "sub.localhost",
            "broadcasthost",
            "127.0.0.1",
            "[::1]",
            "",
        ],
    )
    def test_blocked(self, host):
        assert hostname_is_blocked_literal(host) is True

    def test_public_hostname_passes_literal(self):
        # Nome non-IP: il literal check non risolve DNS, passa.
        assert hostname_is_blocked_literal("example.com") is False


class TestAssertUrlSafe:
    def test_blocks_bad_scheme(self, monkeypatch):
        with pytest.raises(SsrfError, match="scheme"):
            assert_url_safe("ftp://example.com/x")

    def test_blocks_literal_internal(self):
        with pytest.raises(SsrfError):
            assert_url_safe("http://127.0.0.1/latest/meta-data")

    def test_blocks_dns_resolving_internal(self, monkeypatch):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            _fake_getaddrinfo({"evil.example": ["169.254.169.254"]}),
        )
        with pytest.raises(SsrfError, match="blocked address"):
            assert_url_safe("http://evil.example/")

    def test_blocks_partial_internal_resolution(self, monkeypatch):
        # ANY resolved address internal → blocked (fail-closed su rebinding round-robin)
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            _fake_getaddrinfo({"mixed.example": ["8.8.8.8", "127.0.0.1"]}),
        )
        with pytest.raises(SsrfError):
            assert_url_safe("https://mixed.example/")

    def test_blocks_unresolvable(self, monkeypatch):
        monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({}))
        with pytest.raises(SsrfError, match="resolve"):
            assert_url_safe("https://nxdomain.example/")

    def test_allows_public(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"ok.example": ["93.184.216.34"]})
        )
        assert_url_safe("https://ok.example/path")

    def test_allow_internal_skips_dns(self, monkeypatch):
        # Con allow_internal niente risoluzione: scheme comunque enforced.
        policy = SsrfPolicy(allow_internal=True)
        assert_url_safe("http://127.0.0.1:8080/", policy)
        with pytest.raises(SsrfError, match="scheme"):
            assert_url_safe("file:///etc/passwd", policy)

    def test_allowed_hosts_restricts(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"api.example": ["93.184.216.34"]})
        )
        policy = SsrfPolicy(allowed_hosts=frozenset({"api.example"}))
        assert_url_safe("https://api.example/v1", policy)
        with pytest.raises(SsrfError, match="not in the allowed host list"):
            assert_url_safe("https://other.example/v1", policy)

    def test_blocks_dns_label_too_long(self):
        # DNS label >255 chars raises UnicodeError, must be caught and wrapped
        long_label = "x" * 256
        with pytest.raises(SsrfError):
            assert_url_safe(f"https://{long_label}.example/")

    def test_blocks_port_out_of_range(self):
        # Port >65535 raises ValueError, must be caught and wrapped
        with pytest.raises(SsrfError, match="Invalid URL port"):
            assert_url_safe("https://example.com:99999999/")

    async def test_async_variant(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"ok.example": ["93.184.216.34"]})
        )
        await assert_url_safe_async("https://ok.example/")
        with pytest.raises(SsrfError):
            await assert_url_safe_async("http://localhost/")


class TestResolvePinnedTarget:
    def test_pins_first_safe_ip(self, monkeypatch):
        monkeypatch.setattr(
            socket, "getaddrinfo", _fake_getaddrinfo({"ok.example": ["93.184.216.34"]})
        )
        pinned, host = resolve_pinned_target("https://ok.example:8443/p?q=1")
        assert pinned == "https://93.184.216.34:8443/p?q=1"
        assert host == "ok.example"

    def test_ipv6_pin_brackets(self, monkeypatch):
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            _fake_getaddrinfo({"v6.example": ["2606:4700::1111"]}),
        )
        pinned, host = resolve_pinned_target("https://v6.example/x")
        assert pinned == "https://[2606:4700::1111]/x"

    def test_allow_internal_returns_original(self):
        policy = SsrfPolicy(allow_internal=True)
        pinned, host = resolve_pinned_target("http://192.168.1.10/hook", policy)
        assert pinned == "http://192.168.1.10/hook"
        assert host == "192.168.1.10"
