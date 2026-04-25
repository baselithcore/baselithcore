"""Tests for the BrowserAgent SSRF guard."""

from __future__ import annotations

import pytest

from plugins.browser_agent.agent import (
    _hostname_is_blocked,
    _ssrf_guard_disabled,
    assert_navigation_allowed,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/path",
        "http://api.openai.com/v1/chat",
        "https://8.8.8.8/resource",
    ],
)
def test_allows_public_urls(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.delenv("BASELITH_BROWSER_ALLOW_INTERNAL", raising=False)
    assert_navigation_allowed(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost",
        "http://localhost:8080/admin",
        "http://service.localhost",
        "http://127.0.0.1",
        "http://10.0.0.5",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]",
    ],
)
def test_blocks_internal_hosts(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.delenv("BASELITH_BROWSER_ALLOW_INTERNAL", raising=False)
    with pytest.raises(ValueError):
        assert_navigation_allowed(url)


@pytest.mark.parametrize("scheme", ["file", "ftp", "javascript", "data"])
def test_blocks_disallowed_schemes(
    monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    monkeypatch.delenv("BASELITH_BROWSER_ALLOW_INTERNAL", raising=False)
    with pytest.raises(ValueError):
        assert_navigation_allowed(f"{scheme}://anything")


def test_opt_out_env_disables_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BASELITH_BROWSER_ALLOW_INTERNAL", "true")
    assert _ssrf_guard_disabled() is True
    # Should not raise
    assert_navigation_allowed("http://localhost:8080")
    assert_navigation_allowed("http://169.254.169.254/")


def test_hostname_helpers() -> None:
    assert _hostname_is_blocked("localhost") is True
    assert _hostname_is_blocked("127.0.0.1") is True
    assert _hostname_is_blocked("10.0.0.1") is True
    assert _hostname_is_blocked("192.168.0.1") is True
    assert _hostname_is_blocked("169.254.0.1") is True
    assert _hostname_is_blocked("8.8.8.8") is False
    assert _hostname_is_blocked("example.com") is False
    assert _hostname_is_blocked("") is True
