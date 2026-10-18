"""The one network call of the Scrapling fetcher: a GET with a real browser's fingerprint.

``curl_cffi`` is the transport Scrapling's own HTTP fetchers use: it sends a real browser's TLS
and HTTP/2 fingerprint, which is what most bot walls look at. This call adds what Scrapling's
session does not:

- the connection is pinned to the address the SSRF guard verified (curl ``RESOLVE``, so the
  certificate and SNI still name the real host), and the address curl actually connected to is
  checked against it before the body is read: a host curl parses differently from us, so that
  the ``RESOLVE`` entry is ignored and curl resolves it again, fails closed instead of opening;
- redirects are never followed, and the environment's proxy settings are ignored (a proxy would
  bypass the pin);
- curl does not decode the body: it asks for ``gzip, deflate`` only and the body is inflated
  here, never past the cap, so a compression bomb cannot expand in memory before the cap is
  checked; the bytes on the wire are capped as well.
"""

from __future__ import annotations

import ipaddress
import zlib
from dataclasses import dataclass

from baselith.core.security.bounded_body import BOUNDED_ACCEPT_ENCODING

_IDENTITY = ("", "identity")
_ZLIB_WBITS = {
    "gzip": zlib.MAX_WBITS | 16,
    "x-gzip": zlib.MAX_WBITS | 16,
    "deflate": zlib.MAX_WBITS | 32,
}
#: libcurl's CURLE_FILESIZE_EXCEEDED.
_FILESIZE_EXCEEDED = 63


@dataclass(frozen=True, slots=True)
class CurlReply:
    """One HTTP answer: status, lower-cased headers and the (decoded, bounded) body."""

    status: int
    headers: dict[str, str]
    body: bytes


class ResponseTooLargeError(Exception):
    """The body grew past the cap; nothing past it was read."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        super().__init__(f"response body exceeds {limit} bytes")


class UnsupportedEncodingError(Exception):
    """The body uses a ``Content-Encoding`` that cannot be inflated under a cap."""


class PinMismatchError(Exception):
    """curl connected somewhere other than the address the SSRF guard verified."""


def _same_ip(a: str, b: str) -> bool:
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        return False


class _Body:
    """Decoded body under a cap: identity is counted, gzip/deflate inflated stepwise."""

    def __init__(self, encoding: str, max_bytes: int) -> None:
        if encoding not in _IDENTITY and encoding not in _ZLIB_WBITS:
            raise UnsupportedEncodingError(encoding)
        self._max = max_bytes
        self._decoder = (
            None if encoding in _IDENTITY else zlib.decompressobj(_ZLIB_WBITS[encoding])
        )
        self._parts: list[bytes] = []
        self._size = 0

    def _take(self, data: bytes) -> None:
        self._size += len(data)
        if self._size > self._max:
            raise ResponseTooLargeError(self._max)
        self._parts.append(data)

    def feed(self, raw: bytes) -> None:
        if self._decoder is None:
            self._take(raw)
            return
        data = raw
        while data and not self._decoder.eof:
            try:
                out = self._decoder.decompress(data, self._max - self._size + 1)
            except zlib.error as exc:
                raise UnsupportedEncodingError(
                    f"invalid compressed body: {exc}"
                ) from exc
            self._take(out)
            data = self._decoder.unconsumed_tail

    def done(self) -> bytes:
        if self._decoder is not None:
            self._take(self._decoder.flush(self._max - self._size + 1))
        return b"".join(self._parts)


async def curl_get(
    url: str,
    *,
    resolve: list[str],
    expected_ip: str | None,
    timeout: float,
    max_bytes: int,
    impersonate: str,
) -> CurlReply:
    """GET ``url`` once, connecting where ``resolve`` says (``host:port:ip`` entries).

    Raises:
        PinMismatchError: ``expected_ip`` is set and curl connected to another address.
        ResponseTooLargeError: The declared, received or decoded body is over ``max_bytes``.
        UnsupportedEncodingError: A ``Content-Encoding`` other than gzip or deflate, or a
            corrupt compressed body.
    """
    from curl_cffi import CurlOpt
    from curl_cffi.curl import CurlError
    from curl_cffi.requests import AsyncSession

    options: dict[CurlOpt, object] = {CurlOpt.MAXFILESIZE_LARGE: max_bytes}
    if resolve:
        options[CurlOpt.RESOLVE] = resolve
    async with AsyncSession(curl_options=options, trust_env=False) as session:
        try:
            response = await session.request(
                "GET",
                url,
                impersonate=impersonate,  # type: ignore[arg-type]
                # No automatic decoding: the body is inflated here, under the cap.
                accept_encoding=None,
                headers={"Accept-Encoding": BOUNDED_ACCEPT_ENCODING},
                allow_redirects=False,
                stream=True,
                timeout=timeout,
            )
        except CurlError as exc:
            if getattr(exc, "code", None) == _FILESIZE_EXCEEDED:
                raise ResponseTooLargeError(max_bytes) from exc
            raise
        try:
            if expected_ip is not None and not _same_ip(
                str(response.primary_ip), expected_ip
            ):
                raise PinMismatchError(
                    f"connected to {response.primary_ip}, not {expected_ip}"
                )
            headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            encoding = headers.get("content-encoding", "").strip().lower()
            declared = headers.get("content-length", "")
            if (
                encoding in _IDENTITY
                and declared.isdigit()
                and int(declared) > max_bytes
            ):
                raise ResponseTooLargeError(max_bytes)
            body = _Body(encoding, max_bytes)
            try:
                async for chunk in response.aiter_content():
                    body.feed(chunk)
            except CurlError as exc:
                if getattr(exc, "code", None) == _FILESIZE_EXCEEDED:
                    raise ResponseTooLargeError(max_bytes) from exc
                raise
            data = body.done()
        finally:
            await response.aclose()
    return CurlReply(response.status_code, headers, data)


__all__ = [
    "CurlReply",
    "PinMismatchError",
    "ResponseTooLargeError",
    "UnsupportedEncodingError",
    "curl_get",
]
