"""Size-bounded reading of HTTP response bodies, compressed ones included.

A byte counter on ``httpx.Response.aiter_bytes()`` does not bound memory for
a compressed body: httpx inflates each network chunk whole before yielding
it, so a few KiB of gzip, brotli or zstd on the wire become gigabytes before
the counter is consulted. :func:`aiter_bounded` reads the raw (wire) bytes
instead and inflates them itself, never producing more than the cap allows:

* ``identity`` (or no ``Content-Encoding``) passes through;
* ``gzip`` and ``deflate`` are inflated with ``zlib`` under a ``max_length``,
  so each step yields at most the bytes still allowed;
* anything else (``br``, ``zstd``, stacked encodings) is refused before the
  body is read — no bounded decoder exists for it here.

Requests that read through this module should send
:data:`BOUNDED_ACCEPT_ENCODING`, so a server never picks an encoding the
reader would refuse.
"""

from __future__ import annotations

import zlib
from collections.abc import AsyncIterator

import httpx

BOUNDED_ACCEPT_ENCODING = "gzip, deflate"
"""The ``Accept-Encoding`` to send for a body read through this module."""

_IDENTITY = ("", "identity")
_ZLIB_WBITS = {
    "gzip": zlib.MAX_WBITS | 16,
    "x-gzip": zlib.MAX_WBITS | 16,
    # RFC 9110 deflate is zlib-wrapped; 32 + MAX_WBITS also detects gzip.
    "deflate": zlib.MAX_WBITS | 32,
}


class BodyLimitError(ValueError):
    """A response body the bounded reader refuses."""


class BodyTooLargeError(BodyLimitError):
    """The body (declared or decoded) is larger than the cap."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"response body exceeds {limit} bytes")
        self.limit = limit


class UnsupportedEncodingError(BodyLimitError):
    """The body uses a ``Content-Encoding`` with no bounded decoder."""

    def __init__(self, encoding: str) -> None:
        super().__init__(
            f"response uses Content-Encoding {encoding!r}; only identity, "
            "gzip and deflate can be read under a size cap"
        )
        self.encoding = encoding


def body_encoding(response: httpx.Response) -> str:
    """The response's ``Content-Encoding``, normalised; refused when unbounded."""
    encoding = str(response.headers.get("content-encoding", "")).strip().lower()
    if encoding in _IDENTITY or encoding in _ZLIB_WBITS:
        return encoding
    raise UnsupportedEncodingError(encoding)


def check_declared_length(response: httpx.Response, limit: int) -> None:
    """Refuse up front a ``Content-Length`` over *limit*.

    The header counts wire bytes, so it only proves an identity body too large;
    a compressed one is bounded while it is inflated.
    """
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        if response.headers.get("content-encoding", "").strip().lower() in _IDENTITY:
            raise BodyTooLargeError(limit)


async def _inflate(
    response: httpx.Response, encoding: str, limit: int
) -> AsyncIterator[bytes]:
    """Yield decoded chunks; stop once more than *limit* bytes were produced.

    Each inflation step is capped at the bytes still allowed plus one, so the
    decoder never materialises more than ``limit + 1`` bytes in total (an
    identity chunk is yielded as received: it is already in memory).
    """
    if hasattr(response, "_content"):
        # Already read (and decoded) by the caller: nothing left to bound.
        if response.content:
            yield response.content
        return
    if encoding in _IDENTITY:
        total = 0
        async for chunk in response.aiter_raw():
            total += len(chunk)
            yield chunk
            if total > limit:
                return
        return
    decoder = zlib.decompressobj(_ZLIB_WBITS[encoding])
    total = 0
    async for raw in response.aiter_raw():
        data = raw
        while data and not decoder.eof:
            try:
                out = decoder.decompress(data, limit - total + 1)
            except zlib.error as exc:
                raise httpx.DecodingError(f"invalid {encoding} body: {exc}") from exc
            total += len(out)
            if out:
                yield out
            if total > limit:
                return
            data = decoder.unconsumed_tail
    tail = decoder.flush(limit - total + 1)
    if tail:
        yield tail


async def aiter_bounded(response: httpx.Response, limit: int) -> AsyncIterator[bytes]:
    """Yield the decoded body of a streamed *response*, never past *limit* bytes.

    Raises:
        UnsupportedEncodingError: Before reading, for an encoding without a
            bounded decoder.
        BodyTooLargeError: As soon as the decoded body would pass *limit*
            (or up front, from an identity ``Content-Length``).
    """
    encoding = body_encoding(response)
    check_declared_length(response, limit)
    total = 0
    async for chunk in _inflate(response, encoding, limit):
        total += len(chunk)
        if total > limit:
            raise BodyTooLargeError(limit)
        yield chunk


async def read_truncated(response: httpx.Response, limit: int) -> bytes:
    """The first *limit* decoded bytes of *response*; the rest is never read.

    For bodies where a prefix is still useful (``robots.txt``): an oversized
    body is cut, not refused. An unbounded encoding is still refused.
    """
    encoding = body_encoding(response)
    body = bytearray()
    async for chunk in _inflate(response, encoding, limit):
        body += chunk
        if len(body) >= limit:
            break
    return bytes(body[:limit])


async def read_bounded(response: httpx.Response, limit: int) -> bytes:
    """Read a streamed *response* body through :func:`aiter_bounded`."""
    body = bytearray()
    async for chunk in aiter_bounded(response, limit):
        body += chunk
    return bytes(body)


def decoded_headers(response: httpx.Response) -> list[tuple[str, str]]:
    """The response headers minus those describing the wire encoding.

    For rebuilding a buffered ``httpx.Response`` from a decoded body.
    """
    return [
        (key, value)
        for key, value in response.headers.multi_items()
        if key.lower() not in ("content-encoding", "content-length")
    ]


__all__ = [
    "BOUNDED_ACCEPT_ENCODING",
    "BodyLimitError",
    "BodyTooLargeError",
    "UnsupportedEncodingError",
    "aiter_bounded",
    "body_encoding",
    "check_declared_length",
    "decoded_headers",
    "read_bounded",
    "read_truncated",
]
