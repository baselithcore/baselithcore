"""The httpx fetcher's IP-pinned request must still speak the original host.

Pinning rewrites the URL to a verified IP (anti DNS-rebinding), so both the
``Host`` header and the TLS SNI have to be restored from the original name —
otherwise the certificate is validated against the IP literal and every HTTPS
fetch dies with ``CERTIFICATE_VERIFY_FAILED: IP address mismatch``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from plugins.web_scraper.fetchers import httpx_fetcher as fetcher_module
from plugins.web_scraper.fetchers.httpx_fetcher import HttpxFetcher

_PINNED = "https://93.184.216.34/post"
_ORIGINAL = "https://example.com/post"


@pytest.fixture()
def seen() -> list[httpx.Request]:
    return []


@pytest.fixture()
def fetcher(
    monkeypatch: pytest.MonkeyPatch, seen: list[httpx.Request]
) -> HttpxFetcher:
    """A fetcher whose DNS pinning and transport are both under test control."""
    monkeypatch.setattr(
        fetcher_module,
        "get_pinned_url_for_host",
        lambda url: (_PINNED, "example.com"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, html="<html><body>ok</body></html>")

    instance = HttpxFetcher()
    instance._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return instance


async def test_pinned_request_carries_the_original_host_as_tls_sni(
    fetcher: HttpxFetcher, seen: list[httpx.Request]
) -> None:
    await fetcher.fetch(_ORIGINAL)

    request = seen[0]
    assert str(request.url) == _PINNED
    assert request.extensions.get("sni_hostname") == "example.com"


async def test_pinned_request_still_sends_the_original_host_header(
    fetcher: HttpxFetcher, seen: list[httpx.Request]
) -> None:
    await fetcher.fetch(_ORIGINAL)

    assert seen[0].headers["Host"] == "example.com"


async def test_redirect_hop_repins_sni_to_the_new_host(
    monkeypatch: pytest.MonkeyPatch, seen: list[httpx.Request]
) -> None:
    """Each hop is re-resolved, so each hop carries its own SNI."""
    pins = {
        _ORIGINAL: ("https://93.184.216.34/post", "example.com"),
        "https://other.test/moved": ("https://93.184.216.35/moved", "other.test"),
    }
    monkeypatch.setattr(
        fetcher_module, "get_pinned_url_for_host", lambda url: pins[url]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(302, headers={"Location": "https://other.test/moved"})
        return httpx.Response(200, html="<html><body>ok</body></html>")

    instance: Any = HttpxFetcher()
    instance._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await instance.fetch(_ORIGINAL)

    assert [r.extensions.get("sni_hostname") for r in seen] == [
        "example.com",
        "other.test",
    ]
