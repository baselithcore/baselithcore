"""The Scrapling fetcher: a real browser's TLS fingerprint, under the scraper's own SSRF rules.

Every hop is checked and pinned (curl ``RESOLVE`` keeps the TLS name the real host), redirects
are followed by hand so each target is checked again, and the body is read through a cap.
"""

from __future__ import annotations

import gzip
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from baselith.core.security.ssrf import SsrfError, SsrfPolicy
from baselith.plugins.web_scraper.fetchers import FetchError
from baselith.plugins.web_scraper.fetchers import scrapling_fetcher as module
from baselith.plugins.web_scraper.fetchers._curl import (
    CurlReply,
    PinMismatchError,
    ResponseTooLargeError,
    UnsupportedEncodingError,
    curl_get,
)
from baselith.plugins.web_scraper.fetchers.scrapling_fetcher import ScraplingFetcher

PAGE = (
    b"<html><head><title>T</title></head><body><main><p>Hello</p></main></body></html>"
)


class Transport:
    """Answers each hop from a script and records what it was asked."""

    def __init__(self, *replies: CurlReply) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, url: str, **kwargs: Any) -> CurlReply:
        self.calls.append({"url": url, **kwargs})
        return self.replies.pop(0)


def pin_to(ip: str) -> Any:
    def pin(url: str) -> tuple[str, str] | None:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        if parts.hostname and parts.hostname.startswith("internal"):
            return None
        host = f"[{ip}]" if ":" in ip else ip
        netloc = f"{host}:{parts.port}" if parts.port else host
        return parts._replace(netloc=netloc).geturl(), parts.hostname or ""

    return pin


@pytest.fixture(autouse=True)
def _installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "scrapling_available", lambda: True)
    monkeypatch.setattr(module, "get_pinned_url_for_host", pin_to("93.184.216.34"))


async def test_a_page_is_fetched_pinned_with_a_browser_fingerprint():
    transport = Transport(CurlReply(200, {"content-type": "text/html"}, PAGE))
    page = await ScraplingFetcher(transport=transport).fetch("https://example.com/post")
    call = transport.calls[0]
    assert call["url"] == "https://example.com/post"
    assert call["resolve"] == ["example.com:443:93.184.216.34"]
    assert call["impersonate"] == "chrome"
    assert call["expected_ip"] == "93.184.216.34"
    assert call["max_bytes"] == module.MAX_BYTES
    assert page.status_code == 200 and page.is_success
    assert page.final_url == "https://example.com/post" and "Hello" in page.html


async def test_each_redirect_is_checked_and_pinned_again():
    transport = Transport(
        CurlReply(302, {"location": "/next"}, b""),
        CurlReply(301, {"location": "https://other.example:8443/final"}, b""),
        CurlReply(200, {}, PAGE),
    )
    page = await ScraplingFetcher(transport=transport).fetch("http://example.com/start")
    assert [c["url"] for c in transport.calls] == [
        "http://example.com/start",
        "http://example.com/next",
        "https://other.example:8443/final",
    ]
    assert transport.calls[2]["resolve"] == ["other.example:8443:93.184.216.34"]
    assert page.final_url == "https://other.example:8443/final"


async def test_an_internal_target_is_refused_first_hop_or_redirect():
    with pytest.raises(FetchError) as first:
        await ScraplingFetcher(transport=Transport()).fetch("http://internal.corp/")
    assert first.value.status_code == 403
    transport = Transport(
        CurlReply(302, {"location": "http://internal.corp/admin"}, b"")
    )
    with pytest.raises(FetchError) as hop:
        await ScraplingFetcher(transport=transport).fetch("http://example.com/")
    assert hop.value.status_code == 403 and "redirect" in hop.value.message


async def test_a_redirect_loop_stops():
    loop = [CurlReply(302, {"location": "/again"}, b"") for _ in range(20)]
    with pytest.raises(FetchError, match="Too many redirects"):
        await ScraplingFetcher(transport=Transport(*loop)).fetch("http://example.com/")


async def test_ipv6_pins_are_bracketed_and_an_unpinned_url_resolves_nothing(
    monkeypatch,
):
    monkeypatch.setattr(module, "get_pinned_url_for_host", pin_to("2001:db8::1"))
    transport = Transport(CurlReply(200, {}, PAGE))
    await ScraplingFetcher(transport=transport).fetch("https://example.com/")
    assert transport.calls[0]["resolve"] == ["example.com:443:[2001:db8::1]"]
    assert transport.calls[0]["expected_ip"] == "2001:db8::1"
    # allow_internal returns the URL unchanged: nothing to pin.
    monkeypatch.setattr(
        module, "get_pinned_url_for_host", lambda url: (url, "example.com")
    )
    transport = Transport(CurlReply(200, {}, PAGE))
    await ScraplingFetcher(transport=transport).fetch("https://example.com/")
    assert transport.calls[0]["resolve"] == []
    assert transport.calls[0]["expected_ip"] is None


async def test_a_body_over_the_cap_and_a_missing_library_are_fetch_errors(monkeypatch):
    class TooBig:
        async def __call__(self, url: str, **kwargs: Any) -> CurlReply:
            raise ResponseTooLargeError(kwargs["max_bytes"])

    with pytest.raises(FetchError) as big:
        await ScraplingFetcher(transport=TooBig()).fetch("https://example.com/")
    assert big.value.status_code == 413
    monkeypatch.setattr(module, "scrapling_available", lambda: False)
    with pytest.raises(FetchError, match="scrapling"):
        await ScraplingFetcher(transport=Transport()).fetch("https://example.com/")


async def test_a_connection_elsewhere_and_an_address_with_credentials_are_refused():
    class Elsewhere:
        async def __call__(self, url: str, **kwargs: Any) -> CurlReply:
            raise PinMismatchError("connected to 10.0.0.5")

    with pytest.raises(FetchError) as moved:
        await ScraplingFetcher(transport=Elsewhere()).fetch("https://example.com/")
    assert moved.value.status_code == 403
    transport = Transport()
    with pytest.raises(FetchError) as creds:
        await ScraplingFetcher(transport=transport).fetch(
            "https://user:pw@example.com/"
        )
    assert creds.value.status_code == 403 and transport.calls == []


async def test_an_unreadable_encoding_is_a_fetch_error():
    class Brotli:
        async def __call__(self, url: str, **kwargs: Any) -> CurlReply:
            raise UnsupportedEncodingError("br")

    with pytest.raises(FetchError) as bad:
        await ScraplingFetcher(transport=Brotli()).fetch("https://example.com/")
    assert bad.value.status_code == 415


async def test_a_callers_policy_replaces_the_scrapers_own(monkeypatch):
    seen: list[SsrfPolicy] = []

    def refuse(url: str, policy: SsrfPolicy) -> tuple[str, str]:
        seen.append(policy)
        raise SsrfError("internal")

    def scraper_pin(url: str) -> None:
        raise AssertionError("the scraper's own policy must not be used")

    monkeypatch.setattr(module, "resolve_pinned_target", refuse)
    monkeypatch.setattr(module, "get_pinned_url_for_host", scraper_pin)
    policy = SsrfPolicy(allow_internal=False)
    with pytest.raises(FetchError) as blocked:
        await ScraplingFetcher(transport=Transport(), policy=policy).fetch(
            "http://intra.test/"
        )
    assert blocked.value.status_code == 403 and seen == [policy]


# -- the real curl transport against a local server --------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def _encoded(self, body: bytes, encoding: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Encoding", encoding)
        self.send_header("X-Accept-Encoding", self.headers.get("Accept-Encoding", ""))
        self.end_headers()
        self.wfile.write(body)

    def _get(self) -> None:
        if self.path == "/gzip":
            return self._encoded(gzip.compress(PAGE), "gzip")
        if self.path == "/bomb":
            return self._encoded(gzip.compress(b"0" * 5_000_000), "gzip")
        if self.path == "/br":
            return self._encoded(b"not really brotli", "br")
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.end_headers()
            return
        body = PAGE if self.path != "/big" else b"x" * 50_000
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("X-Host-Seen", self.headers.get("Host", ""))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return None

    do_GET = _get  # the http.server hook name


@pytest.fixture
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


async def test_curl_connects_to_the_pinned_address_under_the_real_host_name(server):
    pytest.importorskip("curl_cffi")
    url = f"http://pinned.example:{server}/page"
    reply = await curl_get(
        url,
        resolve=[f"pinned.example:{server}:127.0.0.1"],
        expected_ip="127.0.0.1",
        timeout=10,
        max_bytes=100_000,
        impersonate="chrome",
    )
    assert reply.status == 200 and reply.body == PAGE
    assert reply.headers["x-host-seen"] == f"pinned.example:{server}"


async def test_curl_does_not_follow_redirects_and_stops_at_the_cap(server):
    pytest.importorskip("curl_cffi")
    base = f"http://pinned.example:{server}"
    resolve = [f"pinned.example:{server}:127.0.0.1"]
    hop = await curl_get(
        f"{base}/redirect",
        resolve=resolve,
        expected_ip="127.0.0.1",
        timeout=10,
        max_bytes=1000,
        impersonate="chrome",
    )
    assert hop.status == 302 and hop.headers["location"] == "/elsewhere"
    with pytest.raises(ResponseTooLargeError):
        await curl_get(
            f"{base}/big",
            resolve=resolve,
            expected_ip="127.0.0.1",
            timeout=10,
            max_bytes=1000,
            impersonate="chrome",
        )


async def test_curl_refuses_a_connection_to_another_address(server):
    pytest.importorskip("curl_cffi")
    with pytest.raises(PinMismatchError):
        await curl_get(
            f"http://pinned.example:{server}/page",
            resolve=[f"pinned.example:{server}:127.0.0.1"],
            expected_ip="93.184.216.34",
            timeout=10,
            max_bytes=100_000,
            impersonate="chrome",
        )


async def test_curl_inflates_under_the_cap_and_refuses_a_bomb_or_unknown_encoding(
    server,
):
    pytest.importorskip("curl_cffi")
    base = f"http://pinned.example:{server}"
    kw: dict[str, Any] = {
        "resolve": [f"pinned.example:{server}:127.0.0.1"],
        "expected_ip": "127.0.0.1",
        "timeout": 10,
        "impersonate": "chrome",
    }
    page = await curl_get(f"{base}/gzip", max_bytes=100_000, **kw)
    assert page.body == PAGE and page.headers["x-accept-encoding"] == "gzip, deflate"
    with pytest.raises(ResponseTooLargeError):
        await curl_get(f"{base}/bomb", max_bytes=100_000, **kw)
    with pytest.raises(UnsupportedEncodingError):
        await curl_get(f"{base}/br", max_bytes=100_000, **kw)
